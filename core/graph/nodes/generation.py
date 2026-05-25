"""
Generation 节点：NPC 台词生成（工厂模式 + ReAct 工具）。

【通过背包内容约束 AI 幻觉】
- 将 state["npc_inventory"] 转为易读清单（如「治疗药水 x2」）并写入 system prompt，
  使角色明确「当前身上有什么」；模板中 [CURRENT INVENTORY] 与 [CRITICAL REALITY CONSTRAINTS]
  均依赖此清单与 has_healing_potion 等标志位。
- 若背包无药水，has_healing_potion=False，模板会强制输出「不得描述喝药水」等约束，
  从而避免 LLM 编造与背包事实不符的喝药/赠物等动作。
- 物品触发器（如玩家说「给你药水」）在本节点内执行，并写回 flags/背包，保证
  下一轮 prompt 中的背包与标志位与真实状态一致。
"""

import asyncio
import copy
import logging
import os
import time
from collections.abc import Coroutine
from typing import Any, Callable, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from config import settings
from core.engine import generate_dialogue, parse_ai_response
from core.actors import ActorScopedMemoryProvider, build_actor_view
from core.actors.views import ActorView
from core.engine.physics import (
    apply_environment_interaction,
    apply_movement,
    apply_physics,
    execute_loot,
)
from core.eval.telemetry import emit_telemetry, extract_token_usage
from core.graph.graph_state import GameState
from core.graph.nodes.utils import (
    _message_to_dict,
    _msg_content,
    default_entities,
    first_entity_id,
    merge_entities_with_defaults,
    overlay_entity_state,
)
from core.systems.inventory import Inventory, format_inventory_dict_to_display_list, get_registry
from core.systems.mechanics import process_dialogue_triggers
from core.memory.compat import get_default_memory_service
from core.tools.npc_tools import check_target_inventory
from core.utils.text_processor import clean_npc_dialogue, format_history_message, parse_llm_json

# 全局开关：设为 True 时打印发给大模型的 Payload（调试用）
DEBUG_AI_PAYLOAD = False
LLM_TIMEOUT_SECONDS = 4.5
logger = logging.getLogger(__name__)


_CONSUME_ACTION_INTENTS = {"use_item", "consume"}


def _is_generation_speaker_candidate(entity_id: str) -> bool:
    normalized = str(entity_id or "").strip().lower()
    if not normalized:
        return False
    return normalized not in {"player", "unknown"}


def _is_speakable_generation_entity(entity_id: str, entity: Any) -> bool:
    if not _is_generation_speaker_candidate(entity_id):
        return False
    if not isinstance(entity, dict):
        return True
    entity_kind = str(entity.get("entity_type") or entity.get("type") or "").strip().lower()
    if entity_kind in {
        "door",
        "trap",
        "readable",
        "locked_chest",
        "transition_zone",
        "powder_barrel",
        "loot_drop",
        "container",
        "object",
    }:
        return False
    return True


def _resolve_generation_speaker_and_character(
    state: GameState,
    entities: Dict[str, Any],
    requested_speaker: str,
) -> tuple[str, Any]:
    from characters.loader import load_character

    raw_candidates: List[str] = [requested_speaker]
    queue = state.get("speaker_queue")
    if isinstance(queue, list):
        raw_candidates.extend(str(item or "").strip() for item in queue)
    raw_candidates.append(first_entity_id(entities))
    raw_candidates.extend(str(entity_id or "").strip() for entity_id in entities.keys())

    seen: set[str] = set()
    for candidate in raw_candidates:
        normalized = str(candidate or "").strip().lower()
        candidate_entity = entities.get(normalized)
        if not _is_speakable_generation_entity(normalized, candidate_entity) or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return normalized, load_character(normalized)
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "Generation speaker '%s' is not loadable, trying next candidate: %s",
                normalized,
                exc,
            )
            continue

    raise FileNotFoundError(
        "No loadable NPC speaker found for generation node."
    )


def _player_message_suggests_item_offer(text: str) -> bool:
    """玩家是否在口头赠送/递物品（DM 常仍标为 CHAT，但必须走带工具的 Agent）。"""
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    low = t.lower()
    zh = ("给你", "送你", "拿好", "接着", "喝下", "收下", "接住", "一瓶", "这瓶", "把这", "治疗药水", "药水", "东西给你")
    en = ("give you", "here's", "here is", "take this", "take the", "healing potion", "have a potion")
    return any(k in t for k in zh) or any(k in low for k in en)


def _latest_roll_is_meaningful(latest_roll: Any) -> bool:
    """是否存在需要 NPC 严肃接检定结果的掷骰上下文（空 dict 不视为有检定）。"""
    if latest_roll is None:
        return False
    if not isinstance(latest_roll, dict):
        return bool(latest_roll)
    return bool(latest_roll.get("result")) or bool(latest_roll.get("intent"))


def _llm_consume_action_allowed(intent: str, user_input: str) -> bool:
    """
    NPC dialogue JSON is allowed to consume items only when the player explicitly
    requested item use. Combat/spell/move turns are already resolved by mechanics.
    """
    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent in _CONSUME_ACTION_INTENTS:
        return True

    text = str(user_input or "").strip().lower()
    explicit_use_markers = (
        "喝",
        "喝下",
        "服用",
        "使用治疗药水",
        "用治疗药水",
        "治疗药水",
        "药水",
        "drink",
        "potion",
        "use healing",
    )
    return any(marker in text for marker in explicit_use_markers)


def _execute_json_action(
    physical_action: dict,
    speaker: str,
    current_entities: dict,
    player_inv_for_physics: dict,
    current_env_objs: dict,
    *,
    intent: str = "",
    user_input: str = "",
) -> List[Any]:
    """解析并执行 JSON 中的内联物理动作"""
    tool_physics_events: List[Any] = []
    if not physical_action or not isinstance(physical_action, dict):
        return tool_physics_events

    action_type = physical_action.get("action_type")
    if action_type == "transfer_item":
        item_transfers = [
            {
                "from": physical_action.get("source_id", "player"),
                "to": physical_action.get("target_id", speaker),
                "item_id": physical_action.get("item_id", ""),
                "count": int(physical_action.get("amount", 1)),
            }
        ]
        new_events = apply_physics(current_entities, player_inv_for_physics, item_transfers, [])
        tool_physics_events.extend(new_events)
    elif action_type == "interact_object":
        _tid = physical_action.get("target_id", "")
        _detail = (physical_action.get("action_detail") or "").strip() or "open"
        interaction_events = apply_environment_interaction(current_env_objs, _tid, _detail, speaker)
        tool_physics_events.extend(interaction_events)
    elif action_type == "move_to":
        _loc = (physical_action.get("target_id") or "").strip()
        move_events = apply_movement(current_entities, speaker, _loc)
        tool_physics_events.extend(move_events)
    elif action_type == "loot":
        char_id = (
            physical_action.get("character_id")
            or physical_action.get("character")
            or speaker
        )
        obj_id = (
            physical_action.get("target_object")
            or physical_action.get("target_id")
            or "iron_chest"
        )
        loot_log = execute_loot(current_entities, current_env_objs, str(char_id), str(obj_id))
        tool_physics_events.append(loot_log)
    elif action_type in ("consume", "use_item", "use", "drink"):
        if not _llm_consume_action_allowed(intent, user_input):
            print(
                "⚠️ [安全拦截] 忽略 LLM 旁白越权物品消耗: "
                f"speaker={speaker}, intent={intent}, action={physical_action}"
            )
            return tool_physics_events
        item_id = (physical_action.get("item_id") or "").strip()
        if item_id:
            count = int(physical_action.get("amount", 1) or 1)
            item_transfers = [
                {
                    "from": speaker,
                    "to": "consumed",
                    "item_id": item_id,
                    "count": max(1, count),
                }
            ]
            hp_changes: List[Any] = []
            if "healing_potion" in item_id:
                hp_changes.append({"target": speaker, "amount": 5})
            consume_events = apply_physics(
                current_entities, player_inv_for_physics, item_transfers, hp_changes
            )
            tool_physics_events.extend(consume_events)

    return tool_physics_events


def _build_dynamic_context_prompt(actor_view: ActorView, latest_roll: Any) -> str:
    """组装环境感知、ActorView 记忆摘要与骰子检定结果的动态提示词。"""
    context_prompt = ""

    # 1. 环境感知注入
    loc = actor_view.current_location or "Unknown Location"
    env_objs = actor_view.visible_environment_objects or {}
    context_prompt += "\n[CURRENT ENVIRONMENT]\n"
    context_prompt += f"You are currently at: {loc}\n"
    if env_objs:
        context_prompt += "Interactive Objects around you:\n"
        waypoint_ids = []
        for obj_id, obj_data in env_objs.items():
            if isinstance(obj_data, dict):
                waypoint_ids.append(obj_id)
                context_prompt += (
                    f"- {obj_id} ({obj_data.get('name')}): "
                    f"Status=[{obj_data.get('status')}], Desc=[{obj_data.get('description')}]\n"
                )
        if waypoint_ids:
            context_prompt += (
                "\nSemantic waypoints (valid `target_id` for physical_action JSON with "
                f"action_type \"move_to\"): {', '.join(waypoint_ids)}. "
                "Use these exact ids as target_id when moving your character.\n"
            )
    else:
        context_prompt += "There are no notable interactive objects around.\n"

    # 2. ActorView memory snippets 注入
    if actor_view.memory_snippets:
        context_prompt += "\n[LONG-TERM EPISODIC MEMORIES]\n"
        context_prompt += "These are your past memories related to the current situation:\n"
        for mem in actor_view.memory_snippets:
            context_prompt += f"- {mem}\n"
        context_prompt += "Use these memories to inform your reaction if they are relevant.\n"

    # 3. 掷骰检定结果注入
    if latest_roll and isinstance(latest_roll, dict) and _latest_roll_is_meaningful(latest_roll):
        _roll_result = latest_roll.get("result")
        _roll_result_dict = _roll_result if isinstance(_roll_result, dict) else {}
        is_success = bool(_roll_result_dict.get("is_success", False))
        roll_status = "SUCCESS" if is_success else "FAILURE"
        context_prompt += (
            f"\n🚨 [CRITICAL SYSTEM ALERT]: The player just attempted a skill check "
            f"({latest_roll.get('intent')}). The result was: {roll_status}!\n"
        )
        if not is_success:
            context_prompt += (
                "Because the roll is a FAILURE, you MUST absolutely reject the player and their item "
                "in your response. DO NOT ACCEPT IT.\n"
            )
        else:
            context_prompt += (
                "Because the roll is a SUCCESS, you MUST completely COMPLY with the player's command in this turn. "
                "Do NOT lie about your inventory, do NOT hoard items, and do NOT refuse. "
                "You must immediately output the required `physical_action` (like `consume` or `transfer_item`) "
                "to execute the task.\n"
            )

    return context_prompt


def _collect_visible_item_ids_from_inventory(raw_inventory: Any, registry: Any) -> set[str]:
    item_ids: set[str] = set()
    if isinstance(raw_inventory, dict):
        for item_id, count in raw_inventory.items():
            try:
                qty = int(count)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            resolved = registry.resolve_item_id(item_id)
            if resolved:
                item_ids.add(resolved)
        return item_ids

    if isinstance(raw_inventory, list):
        for item in raw_inventory:
            if isinstance(item, dict):
                resolved = registry.resolve_item_id(item.get("id"))
            else:
                resolved = registry.resolve_item_id(item)
            if resolved:
                item_ids.add(resolved)
        return item_ids

    resolved = registry.resolve_item_id(raw_inventory)
    if resolved:
        item_ids.add(resolved)
    return item_ids


def _build_actor_visible_item_lore(actor_view: ActorView) -> str:
    """
    构造仅基于当前 Actor 可见范围的物品知识：
    1) 自身背包
    2) 可见环境对象上的公开物品（inventory/items/item_id）
    """
    registry = get_registry()
    known_items: set[str] = set()

    known_items.update(
        _collect_visible_item_ids_from_inventory(actor_view.self_state.inventory, registry)
    )

    for obj_data in (actor_view.visible_environment_objects or {}).values():
        if not isinstance(obj_data, dict):
            continue
        known_items.update(
            _collect_visible_item_ids_from_inventory(obj_data.get("inventory"), registry)
        )
        known_items.update(
            _collect_visible_item_ids_from_inventory(obj_data.get("items"), registry)
        )
        known_items.update(
            _collect_visible_item_ids_from_inventory(obj_data.get("item_id"), registry)
        )

    if not known_items:
        return ""

    item_lore = (
        "\n\n[CRITICAL KNOWLEDGE: ITEM DATABASE]\n"
        "Here is the real data for the items currently in the game. "
        "Use their translated names and respect their effects/descriptions:\n"
    )
    for item_id in sorted(known_items):
        data = registry.get(item_id)
        item_lore += (
            f"- ID: {item_id} | Name: {data.get('name')} | "
            f"Desc: {data.get('description')} | Effect: {data.get('effect', 'None')}\n"
        )
    return item_lore


def _prompt_environment() -> Environment:
    prompts_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "llm", "prompts"
    )
    return Environment(
        loader=FileSystemLoader(prompts_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _debug_print_messages(messages: List[BaseMessage], label: str = "") -> None:
    if not DEBUG_AI_PAYLOAD:
        return

    if label:
        print("\n" + "🔥" * 25)
        print(label)
    else:
        print("\n" + "🔥" * 25)
        print("🚨 正在打印发给大模型的最终 Payload...")
    print(f"📊 当前消息总数: {len(messages)} 条")
    for index, message in enumerate(messages):
        msg_type = message.__class__.__name__
        print(f"\n[{index}] 角色/类型: {msg_type}")
        if msg_type == "SystemMessage":
            content_str = str(getattr(message, "content", "") or "")
            content_preview = content_str.replace("\n", " ")[:100]
            print(f"    📝 内容预览: {content_preview}...")
            print(f"    📏 总字符数: {len(content_str)}")
        elif msg_type == "HumanMessage":
            print(f"    🗣️ 玩家说: {message.content}")
        elif msg_type == "AIMessage":
            print(f"    🤖 AI 文本: {repr(message.content)}")
            if getattr(message, "tool_calls", None):
                print(f"    🔧 携带工具调用意图: {getattr(message, 'tool_calls', None)}")
        elif msg_type == "ToolMessage":
            print(f"    ⚙️ 工具执行结果: {message.content}")
            print(f"    🔗 绑定的 Tool ID: {getattr(message, 'tool_call_id', '缺失!')}")
        else:
            print(f"    ❓ 未知消息内容: {message.content}")
    print("🔥" * 25 + "\n")


def _build_unconscious_response(
    state: GameState,
    speaker: str,
    character: Any,
    entities: Dict[str, Any],
) -> Optional[dict]:
    current_npc_hp = entities.get(speaker, {}).get("hp", 20)
    if current_npc_hp > 0:
        return None

    char_display = character.data.get("name", speaker.capitalize())
    death_msg = f"🩸 {char_display}倒在地上，失去了意识。周围陷入了死寂。"
    prev_responses = list(state.get("speaker_responses", []))
    return {
        "final_response": death_msg,
        "speaker_responses": prev_responses + [(speaker, death_msg)],
        "thought_process": "",
        "messages": [
            HumanMessage(content=state.get("user_input", "")),
            AIMessage(
                content=f"[SYSTEM] {char_display}已经倒在血泊中，失去了意识。你无法再与其交谈。",
                name=speaker,
            ),
        ],
    }


def _extract_inventory_states(
    state: GameState,
    current_npc: Dict[str, Any],
) -> Dict[str, Any]:
    """统一提取玩家/NPC 背包，并兼容旧字段 npc_inventory 回退。"""
    inventory_raw = current_npc.get("inventory")
    if inventory_raw is None:
        fallback_inventory = state.get("npc_inventory")
        inventory_raw = fallback_inventory if isinstance(fallback_inventory, dict) else {}
    npc_inv = dict(inventory_raw) if isinstance(inventory_raw, dict) else {}
    player_inv = state.get("player_inventory", {})
    if not isinstance(player_inv, dict):
        player_inv = {}

    inventory_display_list = format_inventory_dict_to_display_list(npc_inv)
    has_healing_potion = (npc_inv.get("healing_potion", 0) or 0) >= 1
    return {
        "npc_inv": npc_inv,
        "player_inv": dict(player_inv),
        "inventory_display_list": inventory_display_list,
        "has_healing_potion": has_healing_potion,
    }


def _process_dialogue_triggers(
    user_input: str,
    triggers_config: List[Any],
    flags: Dict[str, Any],
    player_inv: Dict[str, Any],
    npc_inv: Dict[str, Any],
    affection: int,
    speaker: str,
    entities: Dict[str, Any],
) -> Dict[str, Any]:
    """处理 dialogue triggers，并将背包/好感度的副作用显式返回。"""
    trigger_result: Dict[str, Any] = {"journal_entries": [], "relationship_delta": 0}
    if not (user_input and triggers_config):
        return {
            "trigger_result": trigger_result,
            "player_inv": dict(player_inv),
            "npc_inv": dict(npc_inv),
            "affection": affection,
            "entities": entities,
        }

    player_inv_obj = Inventory()
    player_inv_obj.from_dict(dict(player_inv) if isinstance(player_inv, dict) else {})
    npc_inv_obj = Inventory()
    npc_inv_obj.from_dict(dict(npc_inv) if isinstance(npc_inv, dict) else {})
    trigger_result = process_dialogue_triggers(
        user_input,
        triggers_config,
        flags,
        ui=None,
        player_inv=player_inv_obj,
        npc_inv=npc_inv_obj,
    )

    updated_player_inv = player_inv_obj.to_dict()
    updated_npc_inv = npc_inv_obj.to_dict()
    relationship_delta = int(trigger_result.get("relationship_delta", 0) or 0)
    updated_affection = affection
    if relationship_delta != 0:
        updated_affection = max(-100, min(100, affection + relationship_delta))
    if speaker in entities and isinstance(entities[speaker], dict):
        entities[speaker]["affection"] = updated_affection
        entities[speaker]["inventory"] = dict(updated_npc_inv)

    return {
        "trigger_result": trigger_result,
        "player_inv": updated_player_inv,
        "npc_inv": updated_npc_inv,
        "affection": updated_affection,
        "entities": entities,
    }


def _build_environmental_awareness(state: GameState) -> Dict[str, Any]:
    """抽取当前位置和可交互对象快照，避免下游直接操作原始 state。"""
    raw_env = state.get("environment_objects") or {}
    current_env_objs: Dict[str, Any] = {}
    if isinstance(raw_env, dict):
        for env_id, env_data in raw_env.items():
            if isinstance(env_data, dict):
                current_env_objs[env_id] = dict(env_data)
    return {
        "current_location": state.get("current_location", "Unknown Location"),
        "current_env_objs": current_env_objs,
    }


def _prepare_runtime_state(
    speaker: str,
    entities: Dict[str, Any],
    npc_inv: Dict[str, Any],
    player_inv: Dict[str, Any],
    affection: int,
    user_input: str,
    current_env_objs: Dict[str, Any],
) -> Dict[str, Any]:
    current_entities: Dict[str, Any] = {}
    for entity_id, entity_data in entities.items():
        if isinstance(entity_data, dict):
            current_entities[entity_id] = dict(entity_data)
    for entity_id in current_entities:
        current_entities[entity_id].setdefault("affection", 0)
        current_entities[entity_id].setdefault("inventory", {})
        current_entities[entity_id].setdefault(
            "position",
            default_entities.get(entity_id, {}).get("position", "camp_center"),
        )
        if not isinstance(current_entities[entity_id].get("inventory"), dict):
            current_entities[entity_id]["inventory"] = {}
    if speaker not in current_entities:
        current_entities[speaker] = copy.deepcopy(
            default_entities.get(
                speaker,
                {
                    "hp": 20,
                    "active_buffs": [],
                    "affection": 0,
                    "inventory": {},
                    "position": "camp_center",
                },
            )
        )
    if user_input:
        current_entities[speaker]["inventory"] = dict(npc_inv)
        current_entities[speaker]["affection"] = affection

    player_inv_for_physics = dict(player_inv)
    return {
        "current_entities": current_entities,
        "player_inv_for_physics": player_inv_for_physics,
        "current_env_objs": current_env_objs,
    }


def _build_physical_action_suffix(
    idle_banter: bool,
    intent: str,
    speaker: str,
    npc_inv: Dict[str, Any],
) -> str:
    """构建物理动作强约束后缀，字面内容必须与旧实现保持一致。"""
    prompt_suffix = "" if idle_banter else "\n*(现在轮到你做出反应了)*"
    if intent not in ("chat", "banter", "trigger_idle_banter"):
        current_inv_str = str(npc_inv) if npc_inv else "Empty"
        prompt_suffix += f"""\n\n🚨 [CRITICAL OVERRIDE - PHYSICAL ACTION REQUIRED]:
[YOUR ABSOLUTE TRUTH]: Your physical inventory exactly contains: {current_inv_str}.
DO NOT hallucinate. DO NOT claim you don't have these items.
If the player commands you to use or give an item you possess, YOU MUST COMPLY immediately and output the `physical_action` JSON.

Listen carefully, {speaker}: Your text output is ONLY YOUR VOICE. It cannot move items, move your body to a new location, or interact with the world.
If you decide to accept an item, walk to a semantic waypoint, OR interact with the environment (like opening a chest or unlocking a door), YOU MUST output the `physical_action` field in your JSON response!

CRITICAL RULE: If the player just rolled a SUCCESSFUL skill check to command you, YOU MUST execute the action IMMEDIATELY in this turn. DO NOT delay it to the next turn, and DO NOT just roleplay doing it in text!

Examples:
1. Taking an item: "physical_action": {{"action_type": "transfer_item", "source_id": "player", "target_id": "{speaker}", "item_id": "healing_potion", "amount": 1}}
2. Interacting with object: "physical_action": {{"action_type": "interact_object", "target_id": "iron_chest", "action_detail": "unlock"}}
3. Moving to a location: "physical_action": {{"action_type": "move_to", "target_id": "camp_fire"}}
4. Looting an opened container (take all items into the acting character's inventory): "physical_action": {{"action_type": "loot", "character_id": "{speaker}", "target_object": "iron_chest"}}
5. Consuming an item (e.g. potion): "physical_action": {{"action_type": "consume", "item_id": "healing_potion"}}
6. Giving an item to someone else: "physical_action": {{"action_type": "transfer_item", "source_id": "{speaker}", "target_id": "scout", "item_id": "rusty_dagger", "amount": 1}}

When the player tells you to take/grab loot from a chest or container you can reach, output `loot` with `character_id` and `target_object` (the environment object id).
When you give an item from your inventory to another character, use `transfer_item` with `source_id` set to your character id. When you drink or eat something from your own inventory, use `consume` with `item_id`.

IF YOU DO NOT INCLUDE THIS FIELD IN YOUR JSON, YOU ARE JUST STANDING STILL AND DOING NOTHING!"""
    return prompt_suffix


def _build_a_to_a_suffix(last_speaker_id: str, last_speaker_text: str) -> str:
    """构建队友互评规则后缀，保留原有字面 Prompt。"""
    return (
        f"\n\n[CRITICAL A-TO-A NOTE: You are part of a group conversation. "
        f"The player just acted, and {last_speaker_id} reacted by saying: '{last_speaker_text}'.\n"
        f"YOUR TASK: Evaluate {last_speaker_id}'s statement based on your personality.\n"
        "- If you STRONGLY DISAGREE, argue with them.\n"
        "- If you AGREE, support or build on their point.\n"
        "- If you think they are being ridiculous, MOCK them.\n"
        "- If the topic is TRIVIAL or you don't care, DO NOT SPEAK. Output ONLY a brief physical action "
        "(e.g., *rolls eyes*, *yawns*, *ignores them*).\n"
        f"React naturally. Address {last_speaker_id} directly if you choose to speak.]"
    )


def _format_history_messages(
    actor_view: ActorView,
    context: Dict[str, Any],
) -> List[Dict[str, str]]:
    """将历史消息格式化为 LLM 输入，并保留旧实现的后缀注入位置。"""
    messages = [
        {
            "role": visible_message.role,
            "content": visible_message.content,
        }
        for visible_message in actor_view.visible_history
    ]
    user_input = context["user_input"]
    if context["is_first_npc_of_player_turn"] and user_input:
        if not messages or str(messages[-1].get("content") or "") != user_input:
            messages.append({"role": "user", "content": user_input})

    recent_messages = messages[-20:] if len(messages) > 20 else messages
    history_dicts = [_message_to_dict(message) for message in recent_messages]

    prompt_suffix = _build_physical_action_suffix(
        idle_banter=context["idle_banter"],
        intent=context["intent"],
        speaker=context["speaker"],
        npc_inv=context["npc_inv"],
    )

    if not context["idle_banter"] and len(context["prev_responses"]) > 0:
        last_speaker_id, last_speaker_text = context["prev_responses"][-1]
        if history_dicts and history_dicts[-1]["role"] == "user":
            original_text = history_dicts[-1]["content"]
            history_dicts[-1]["content"] = (
                f"[事件回顾] 玩家说：{original_text}\n"
                f"[刚刚发生] {last_speaker_id} 回应道：{last_speaker_text}"
                + prompt_suffix
            )
    elif (
        not context["idle_banter"]
        and history_dicts
        and history_dicts[-1].get("role") == "user"
    ):
        last_user_content = history_dicts[-1].get("content") or ""
        history_dicts[-1]["content"] = last_user_content + prompt_suffix

    return history_dicts


def _build_history_dicts(state: GameState, context: Dict[str, Any]) -> List[Dict[str, str]]:
    """兼容旧函数名，委托给新的 history formatter。"""
    actor_view = context.get("actor_view")
    if not isinstance(actor_view, ActorView):
        actor_view = build_actor_view(state, context.get("speaker", "player"))
    return _format_history_messages(actor_view, context)


def _prepare_generation_context(
    state: GameState,
    speaker: str,
    character: Any,
    entities: Dict[str, Any],
    actor_view: Optional[ActorView] = None,
) -> Dict[str, Any]:
    if actor_view is None:
        actor_view = build_actor_view(state, speaker)
    user_input = actor_view.user_input
    current_npc = entities.get(speaker, {})
    affection = current_npc.get("affection", 0)
    flags = actor_view.visible_flags
    inventory_state = _extract_inventory_states(state, current_npc)
    npc_inv = inventory_state["npc_inv"]
    player_inv = inventory_state["player_inv"]
    journal_events = list(actor_view.recent_public_events)
    summary = state.get("summary", "Graph Mode Testing")
    prev_responses = list(state.get("speaker_responses", []))
    is_first_npc_of_player_turn = len(prev_responses) == 0
    environmental_awareness = {
        "current_location": actor_view.current_location or state.get("current_location", "Unknown Location"),
        "current_env_objs": {
            env_id: dict(env_data)
            for env_id, env_data in (actor_view.visible_environment_objects or {}).items()
            if isinstance(env_data, dict)
        },
    }

    intent = str(state.get("intent", "chat") or "chat").strip().lower()
    idle_banter = intent == "trigger_idle_banter"
    latest_roll = actor_view.latest_roll
    banter_allowed_intents = frozenset({"chat", "banter", "trigger_idle_banter"})
    needs_full_agent = (
        intent not in banter_allowed_intents
        or _latest_roll_is_meaningful(latest_roll)
        or bool(actor_view.is_probing_secret)
        or _player_message_suggests_item_offer(user_input)
    )

    messages = list(actor_view.visible_history)
    is_banter = False
    dm_text = ""
    if not needs_full_agent and messages:
        last_msg = messages[-1]
        last_content = str(getattr(last_msg, "content", "") or "")
        last_name = str(getattr(last_msg, "speaker_id", "") or "")
        if last_name == "dm" or (last_content and last_content.strip().startswith("[DM]:")):
            is_banter = True
            dm_text = last_content.replace("[DM]:", "").strip() if last_content else ""

    triggers_config = character.data.get("dialogue_triggers", [])
    trigger_state = _process_dialogue_triggers(
        user_input=user_input,
        triggers_config=triggers_config,
        flags=flags,
        player_inv=player_inv,
        npc_inv=npc_inv,
        affection=affection,
        speaker=speaker,
        entities=entities,
    )
    player_inv = trigger_state["player_inv"]
    npc_inv = trigger_state["npc_inv"]
    affection = trigger_state["affection"]
    trigger_result = trigger_state["trigger_result"]
    inventory_display_list = format_inventory_dict_to_display_list(npc_inv)
    has_healing_potion = (npc_inv.get("healing_potion", 0) or 0) >= 1
    print(f"🚨 [DEBUG 状态核对] Speaker: {speaker}")
    print(f"🚨 [DEBUG 状态核对] npc_inv (字典): {npc_inv}")
    print(f"🚨 [DEBUG 状态核对] has_healing_potion (布尔): {has_healing_potion}")

    current_npc_data = entities.get(speaker, {})
    runtime_state = _prepare_runtime_state(
        speaker=speaker,
        entities=entities,
        npc_inv=npc_inv,
        player_inv=player_inv,
        affection=affection,
        user_input=user_input,
        current_env_objs=environmental_awareness["current_env_objs"],
    )

    context: Dict[str, Any] = {
        "speaker": speaker,
        "character": character,
        "actor_view": actor_view,
        "entities": entities,
        "user_input": user_input,
        "affection": affection,
        "flags": flags,
        "npc_inv": npc_inv,
        "player_inv": player_inv,
        "journal_events": journal_events,
        "summary": summary,
        "prev_responses": prev_responses,
        "is_first_npc_of_player_turn": is_first_npc_of_player_turn,
        "intent": intent,
        "idle_banter": idle_banter,
        "latest_roll": latest_roll,
        "is_banter": is_banter,
        "dm_text": dm_text,
        "triggers_config": triggers_config,
        "trigger_result": trigger_result,
        "inventory_display_list": inventory_display_list,
        "has_healing_potion": has_healing_potion,
        "current_npc_data": current_npc_data,
        "environment": environmental_awareness,
        **runtime_state,
    }
    context["history_dicts"] = _format_history_messages(actor_view, context)
    return context


async def _maybe_generate_banter_response(
    state: GameState,
    context: Dict[str, Any],
) -> Optional[dict]:
    if not (context["is_banter"] and context["dm_text"]):
        return None

    speaker = context["speaker"]
    character = context["character"]
    affection = context["affection"]
    print(f"💬 [Banter Mode] {speaker.capitalize()} 使用极简模板吐槽...")
    banter_tpl = _prompt_environment().get_template("banter.j2")
    traits = character.data.get("personality", {}).get("traits", []) or []
    core_traits = ", ".join(str(trait) for trait in traits[:3]) if traits else "mysterious"
    system_prompt = banter_tpl.render(
        npc_name=character.data.get("name", speaker.capitalize()),
        core_traits=core_traits,
        approval=affection,
        dm_text=context["dm_text"],
    )
    history_dicts = [{"role": "user", "content": f"[DM]: {context['dm_text']}"}]
    try:
        llm_started_at = time.perf_counter()
        raw_response = await asyncio.wait_for(
            asyncio.to_thread(
                generate_dialogue, system_prompt, conversation_history=history_dicts
            ),
            timeout=LLM_TIMEOUT_SECONDS,
        )
        emit_telemetry(
            "llm_call",
            component="generation",
            stage="banter",
            provider="legacy_engine",
            model=settings.MODEL_NAME,
            success=True,
            duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
    except Exception as exc:
        logger.warning("banter generation timed out/failed, fallback line used: %s", exc)
        emit_telemetry(
            "llm_call",
            component="generation",
            stage="banter",
            provider="legacy_engine",
            model=settings.MODEL_NAME,
            success=False,
            error_type=exc.__class__.__name__,
            duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        fallback_text = "……（短暂的沉默在营地里蔓延）"
        clean_text = clean_npc_dialogue(speaker, fallback_text)
        attributed_msg = format_history_message(speaker, clean_text)
        return {
            "final_response": clean_text,
            "speaker_responses": context["prev_responses"] + [(speaker, clean_text)],
            "thought_process": "",
            "messages": [AIMessage(content=attributed_msg, name=speaker)],
        }
    parsed = parse_ai_response(raw_response)
    clean_text = clean_npc_dialogue(speaker, (parsed.get("text") or "...").strip())
    attributed_msg = format_history_message(speaker, clean_text)
    return {
        "final_response": clean_text,
        "speaker_responses": context["prev_responses"] + [(speaker, clean_text)],
        "thought_process": "",
        "messages": [AIMessage(content=attributed_msg, name=speaker)],
    }


def _build_system_prompt(actor_view: ActorView, context: Dict[str, Any]) -> str:
    speaker = context["speaker"]
    character = context["character"]
    idle_banter = context["idle_banter"]
    if idle_banter:
        location = context["environment"]["current_location"]
        party_ids = ", ".join(sorted(context["entities"].keys()))
        system_prompt = (
            "[SYSTEM NOTE - IDLE BANTER MODE]: The player is AFK. You are no longer playing a single character, "
            "but acting as the Omni-Director of the game engine. Your task is to generate a spontaneous ambient "
            "interaction between the NPCs present in the current_location.\n\n"
            "CRITICAL RULES:\n"
            "1. Randomly choose whether to generate a SOLILOQUY (1 character mutters) or a BANTER "
            "(2 characters exchange quick words).\n"
            "2. If BANTER, pick TWO characters from the entities list. Output EXACTLY 2 dialogue objects in the JSON "
            "'responses' array (e.g., Character A says something, Character B replies immediately).\n"
            "3. Keep it extremely short, natural, and passing. Do NOT wait for or address the player.\n\n"
            f"[CONTEXT]\n"
            f"current_location: {location}\n"
            f"entities (valid speaker ids): {party_ids}\n"
        )
    else:
        current_npc_data = context["current_npc_data"]
        system_prompt = character.render_prompt(
            relationship_score=context["affection"],
            affection=context["affection"],
            flags=context["flags"],
            summary=context["summary"],
            journal_entries=context["journal_events"][-5:] if context["journal_events"] else [],
            inventory_items=context["inventory_display_list"],
            has_healing_potion=context["has_healing_potion"],
            time_of_day=actor_view.time_of_day or "晨曦 (Morning)",
            hp=current_npc_data.get("hp", 20),
            active_buffs=current_npc_data.get("active_buffs", []),
            protocol_confidence=current_npc_data.get("protocol_confidence"),
            memory_awakening=current_npc_data.get("memory_awakening"),
        )
        item_lore = _build_actor_visible_item_lore(actor_view)
        if item_lore:
            system_prompt += item_lore
        system_prompt += f"Current Speaker: {speaker}\n"

    system_prompt += f"Player's Current Inventory: {context['player_inv']}\n"
    roll_for_prompt = None if idle_banter else context["latest_roll"]
    system_prompt += _build_dynamic_context_prompt(actor_view, roll_for_prompt)

    if not idle_banter and len(context["prev_responses"]) > 0:
        last_speaker_id, last_speaker_text = context["prev_responses"][-1]
        system_prompt += _build_a_to_a_suffix(last_speaker_id, last_speaker_text)

    rules_tpl = _prompt_environment().get_template("system_rules.j2")
    system_prompt += "\n" + rules_tpl.render() + "\n"

    if idle_banter:
        system_prompt += (
            "\n[OVERRIDE]: For this request only, ignore any single-NPC roleplay or solo `reply` schema "
            "in the rules above. You are the Omni-Director; output only one JSON object with the `responses` array.\n"
            "\nOutput ONLY valid JSON (no markdown fences) with this exact shape:\n"
            '{ "responses": [ {"speaker": "<npc_id>", "text": "<line>"}, ... ] }\n'
            "The array must have length 1 (SOLILOQUY) or 2 (BANTER). Use only speaker ids from [CONTEXT].\n"
            "Do NOT address the player or ask for their input.\n"
        )

    return system_prompt


def _build_lc_messages(system_prompt: str, history_dicts: List[Dict[str, str]]) -> List[BaseMessage]:
    lc_messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    for history_item in history_dicts:
        if history_item.get("role") == "user":
            lc_messages.append(HumanMessage(content=history_item.get("content", "")))
        else:
            lc_messages.append(AIMessage(content=history_item.get("content", "")))
    return lc_messages


def _create_llm_client(idle_banter: bool) -> Any:
    llm = ChatOpenAI(
        model=settings.MODEL_NAME,
        api_key=settings.API_KEY,  # type: ignore[arg-type]
        base_url=settings.BASE_URL,
        temperature=0.7,
        max_completion_tokens=500,
    )
    if idle_banter:
        return llm
    return llm.bind_tools([check_target_inventory])


async def _execute_llm_with_tools(
    llm_with_tools: Any,
    lc_messages: List[BaseMessage],
    player_inv_for_physics: Dict[str, Any],
    current_entities: Dict[str, Any],
    idle_banter: bool,
) -> tuple[Any, List[BaseMessage]]:
    from ui.renderer import GameRenderer

    _debug_print_messages(lc_messages)
    llm_started_at = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            llm_with_tools.ainvoke(lc_messages),
            timeout=LLM_TIMEOUT_SECONDS,
        )
        emit_telemetry(
            "llm_call",
            component="generation",
            stage="initial",
            provider="langchain_openai",
            model=settings.MODEL_NAME,
            success=True,
            duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
            token_usage=extract_token_usage(response),
        )
    except Exception as exc:
        logger.warning("generation LLM invoke timed out/failed, fallback response used: %s", exc)
        emit_telemetry(
            "llm_call",
            component="generation",
            stage="initial",
            provider="langchain_openai",
            model=settings.MODEL_NAME,
            success=False,
            error_type=exc.__class__.__name__,
            duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        return AIMessage(content="*（星界回响短暂中断，未能组织语言）*"), lc_messages
    GameRenderer().print_system_info(
        f"🔧 [底层透视] LLM 返回的 tool_calls: {getattr(response, 'tool_calls', [])}"
    )

    if idle_banter:
        return response, lc_messages

    max_iterations = 5
    iteration_count = 0
    while getattr(response, "tool_calls", None):
        iteration_count += 1
        lc_messages.append(response)
        registry = get_registry()
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args") or {}
            tool_result_str = "操作失败"

            if tool_name == "check_target_inventory":
                target = tool_args.get("target_id", "player")
                keyword = (tool_args.get("item_keyword") or "").lower()
                if target == "player":
                    inventory = player_inv_for_physics
                else:
                    inventory = current_entities.get(target, {}).get("inventory", {})
                inventory = inventory or {}
                found = False
                for item_id, count in inventory.items():
                    if keyword in item_id.lower() or keyword in registry.get_name(item_id).lower():
                        tool_result_str = f"{target} 拥有 {count} 个 {item_id}。"
                        found = True
                        break
                if not found:
                    tool_result_str = (
                        f"{target} 的背包里根本没有找到 '{keyword}'！他在撒谎或两手空空。"
                    )
            else:
                tool_result_str = (
                    f"[系统] 未知工具: {tool_name}（物理动作请使用 JSON 中的 physical_action 字段）。"
                )

            lc_messages.append(
                ToolMessage(content=tool_result_str, tool_call_id=tool_call.get("id", ""))
            )

        _debug_print_messages(
            lc_messages,
            f"🚨 [ReAct 第 {iteration_count} 轮] 正在打印发给大模型的 Payload (含工具返回)...",
        )
        llm_started_at = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                llm_with_tools.ainvoke(lc_messages),
                timeout=LLM_TIMEOUT_SECONDS,
            )
            emit_telemetry(
                "llm_call",
                component="generation",
                stage=f"react_{iteration_count}",
                provider="langchain_openai",
                model=settings.MODEL_NAME,
                success=True,
                duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
                token_usage=extract_token_usage(response),
            )
        except Exception as exc:
            logger.warning(
                "generation ReAct follow-up invoke timed out/failed, fallback response used: %s",
                exc,
            )
            emit_telemetry(
                "llm_call",
                component="generation",
                stage=f"react_{iteration_count}",
                provider="langchain_openai",
                model=settings.MODEL_NAME,
                success=False,
                error_type=exc.__class__.__name__,
                duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            response = AIMessage(content="*（星界回响中断，话语戛然而止）*")
            break
        GameRenderer().print_system_info(
            f"🔧 [底层透视] LLM 返回的 tool_calls (ReAct #{iteration_count}): "
            f"{getattr(response, 'tool_calls', [])}"
        )
        if iteration_count >= max_iterations:
            print("⚠️ [安全拦截] Agent 内部工具调用次数超限 (>=5次)，强制终止循环！")
            break

    if getattr(response, "tool_calls", None) and not (getattr(response, "content", None) or "").strip():
        response = AIMessage(content="*（陷入了深深的沉思，暂时没有回应）*")
    return response, lc_messages


def _parse_and_apply_actions(
    raw_output: str,
    idle_banter: bool,
    speaker: str,
    entities: Dict[str, Any],
    current_entities: Dict[str, Any],
    player_inv_for_physics: Dict[str, Any],
    current_env_objs: Dict[str, Any],
    intent: str = "",
    user_input: str = "",
) -> Dict[str, Any]:
    json_parsed = parse_llm_json(raw_output)
    if DEBUG_AI_PAYLOAD:
        print(f"📦 [底层 JSON 透视] \n{raw_output}\n")

    idle_merged: Optional[List[tuple[str, str]]] = None
    tool_physics_events: List[Any] = []
    if idle_banter and isinstance(json_parsed, dict):
        lines = json_parsed.get("responses") or json_parsed.get("idle_banter_lines")
        if isinstance(lines, list) and lines:
            valid_ids = set(entities.keys())
            merged: List[tuple[str, str]] = []
            for item in lines[:2]:
                if not isinstance(item, dict):
                    continue
                merged_speaker = str(item.get("speaker") or speaker).strip().lower()
                if merged_speaker not in valid_ids:
                    merged_speaker = speaker
                raw_line = (item.get("text") or "...").strip()
                merged.append((merged_speaker, clean_npc_dialogue(merged_speaker, raw_line)))
            if merged:
                idle_merged = merged

    if idle_merged is not None:
        clean_text = "\n".join(text for _, text in idle_merged)
        thought_process = ""
        state_changes: Dict[str, Any] = {}
    elif isinstance(json_parsed, dict) and "reply" in json_parsed:
        raw_text = (json_parsed.get("reply") or "...").strip()
        thought_process = (json_parsed.get("internal_monologue") or "").strip()
        state_changes = json_parsed.get("state_changes") or {}
        physical_action = json_parsed.get("physical_action")
        if physical_action and isinstance(physical_action, dict):
            tool_physics_events.extend(
                _execute_json_action(
                    physical_action,
                    speaker,
                    current_entities,
                    player_inv_for_physics,
                    current_env_objs,
                    intent=intent,
                    user_input=user_input,
                )
            )
        clean_text = clean_npc_dialogue(speaker, raw_text)
    else:
        parsed = parse_ai_response(raw_output)
        raw_text = (parsed.get("text") or raw_output or "...").strip()
        thought_process = (parsed.get("thought") or "").strip()
        state_changes = {
            "affection_delta": parsed.get("approval", 0),
            "protocol_confidence_delta": 0,
            "memory_awakening_delta": 0,
        }
        clean_text = clean_npc_dialogue(speaker, raw_text)

    affection_delta = int(state_changes.get("affection_delta", 0))
    protocol_confidence_delta = int(state_changes.get("protocol_confidence_delta", 0))
    memory_delta = int(state_changes.get("memory_awakening_delta", 0))
    state_changes_applied = False
    if affection_delta != 0 or protocol_confidence_delta != 0 or memory_delta != 0:
        entity_state = dict(current_entities.get(speaker, {}))
        entity_state["affection"] = max(
            -100, min(100, entity_state.get("affection", 0) + affection_delta)
        )
        if "protocol_confidence" in entity_state:
            entity_state["protocol_confidence"] = max(
                0, min(100, entity_state["protocol_confidence"] + protocol_confidence_delta)
            )
        if "memory_awakening" in entity_state:
            entity_state["memory_awakening"] = max(
                0, min(100, entity_state["memory_awakening"] + memory_delta)
            )
        current_entities[speaker] = entity_state
        state_changes_applied = True

    return {
        "clean_text": clean_text,
        "thought_process": thought_process,
        "tool_physics_events": tool_physics_events,
        "state_changes_applied": state_changes_applied,
        "idle_merged": idle_merged,
    }


def _assemble_generation_output(
    state: GameState,
    context: Dict[str, Any],
    parsed_result: Dict[str, Any],
) -> dict:
    speaker = context["speaker"]
    clean_text = parsed_result["clean_text"]
    idle_merged = parsed_result["idle_merged"]
    attributed_msg = ""
    if idle_merged is not None:
        out_messages = [
            AIMessage(content=format_history_message(spk, txt), name=spk)
            for spk, txt in idle_merged
        ]
        if context["is_first_npc_of_player_turn"] and context["user_input"]:
            out_messages = [HumanMessage(content=context["user_input"])] + out_messages
        speaker_responses = context["prev_responses"] + list(idle_merged)
    else:
        attributed_msg = format_history_message(speaker, clean_text)
        out_messages = [AIMessage(content=attributed_msg, name=speaker)]
        if context["is_first_npc_of_player_turn"] and context["user_input"]:
            out_messages = [
                HumanMessage(content=context["user_input"]),
                AIMessage(content=attributed_msg, name=speaker),
            ]
        speaker_responses = context["prev_responses"] + [(speaker, clean_text)]

    out = {
        "final_response": clean_text,
        "speaker_responses": speaker_responses,
        "thought_process": parsed_result["thought_process"],
        "messages": out_messages,
    }
    entities_out = overlay_entity_state(state.get("entities"), context["current_entities"])
    trigger_journal = context["trigger_result"].get("journal_entries", [])
    if trigger_journal:
        out["journal_events"] = out.get("journal_events", []) + list(trigger_journal)
    if parsed_result["tool_physics_events"]:
        out["journal_events"] = out.get("journal_events", []) + parsed_result["tool_physics_events"]
        out["player_inventory"] = context["player_inv_for_physics"]
        out["environment_objects"] = context["current_env_objs"]
    if context["user_input"] and context["triggers_config"]:
        out["flags"] = context["flags"]
        out["player_inventory"] = context["player_inv_for_physics"]
    if (
        parsed_result["state_changes_applied"]
        or parsed_result["tool_physics_events"]
        or (context["user_input"] and context["triggers_config"])
        or context["user_input"]
    ):
        out["entities"] = entities_out
    return out


def create_generation_node() -> Callable[[GameState], Coroutine[Any, Any, dict]]:
    """
    工厂函数：创建 Generation 节点。
    根据 state["current_speaker"] 动态加载 YAML 灵魂，实现多智能体话语权路由。
    """

    async def generation_node(state: GameState) -> dict:
        """
        LLM 生成节点。
        根据 current_speaker 动态加载对应角色，从 state 提取 affection / flags / inventory 等。
        """
        entities = merge_entities_with_defaults(state.get("entities"))
        fallback_speaker = first_entity_id(entities)
        requested_speaker = (state.get("current_speaker") or "").strip() or fallback_speaker
        speaker, character = _resolve_generation_speaker_and_character(
            state,
            entities,
            requested_speaker,
        )
        memory_service = get_default_memory_service()
        actor_view = build_actor_view(
            state,
            speaker,
            memory_provider=ActorScopedMemoryProvider(memory_service.retriever),
        )
        print(f"🗣️ Generation Node: {speaker.capitalize()} is speaking...")
        early_return = _build_unconscious_response(state, speaker, character, entities)
        if early_return is not None:
            return early_return

        context = _prepare_generation_context(
            state,
            speaker,
            character,
            entities,
            actor_view=actor_view,
        )
        banter_response = await _maybe_generate_banter_response(state, context)
        if banter_response is not None:
            return banter_response

        system_prompt = _build_system_prompt(actor_view, context)
        lc_messages = _build_lc_messages(system_prompt, context["history_dicts"])
        llm_with_tools = _create_llm_client(context["idle_banter"])
        response, _ = await _execute_llm_with_tools(
            llm_with_tools=llm_with_tools,
            lc_messages=lc_messages,
            player_inv_for_physics=context["player_inv_for_physics"],
            current_entities=context["current_entities"],
            idle_banter=context["idle_banter"],
        )
        raw_output = str(response.content if hasattr(response, "content") else str(response or ""))
        parsed_result = _parse_and_apply_actions(
            raw_output=raw_output,
            idle_banter=context["idle_banter"],
            speaker=speaker,
            entities=context["entities"],
            current_entities=context["current_entities"],
            player_inv_for_physics=context["player_inv_for_physics"],
            current_env_objs=context["current_env_objs"],
            intent=context.get("intent", ""),
            user_input=context.get("user_input", ""),
        )
        return _assemble_generation_output(state, context, parsed_result)

    return generation_node


async def generation_node(state: GameState) -> dict:
    """
    默认 Generation 节点（向后兼容 main_graph.py 等单测）。
    生产环境应使用 create_generation_node() 动态加载角色。
    """
    return await create_generation_node()(state)
