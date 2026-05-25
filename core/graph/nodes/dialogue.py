"""
对话节点：处理 START_DIALOGUE / DIALOGUE_REPLY 的会话锁与 hostile 模板驱动交涉。
"""

import copy
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from langchain_core.messages import AIMessage

from characters.loader import CharacterLoader
from core.actors import ActorScopedMemoryProvider, build_actor_view
from core.actors.views import ActorView
from core.campaigns import detect_gatekeeper_boss_intro_context
from core.events.models import DomainEvent, event_to_dict
from core.events.store import append_pending_events
from core.graph.graph_state import GameState
from core.memory.compat import get_default_memory_service
from core.systems.inventory import get_registry
from core.systems.mechanics import calculate_ability_modifier
from core.utils.text_processor import format_history_message, parse_llm_json

LLM_TIMEOUT_SECONDS = 4.5


def _run_blocking_with_timeout(func, *args, timeout: float = LLM_TIMEOUT_SECONDS, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            raise TimeoutError("LLM call timeout") from exc


def _normalize_entity_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _display_entity_name(entity_id: str, entity: Dict[str, Any]) -> str:
    name = str(entity.get("name") or "").strip()
    if name:
        return name
    if entity_id == "player":
        return "玩家"
    return entity_id.replace("_", " ").strip().title() or "未知目标"


def _is_alive(entity: Dict[str, Any]) -> bool:
    status = str(entity.get("status", "alive")).strip().lower()
    hp = int(entity.get("hp", 0) or 0)
    return status not in {"dead", "downed", "unconscious"} and hp > 0


def _is_player_side(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    if normalized_id in {"player", "analyst", "scout", "tactician"}:
        return True
    faction = str(entity.get("faction", "")).strip().lower()
    return bool(faction and faction not in {"hostile", "neutral"})


def _normalize_dynamic_states(raw_states: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw_states, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, payload in raw_states.items():
        sid = str(key or "").strip().lower()
        if not sid:
            continue
        if isinstance(payload, dict):
            current = payload.get("current_value", payload.get("value", 0))
            try:
                current_value = int(current)
            except (TypeError, ValueError):
                current_value = 0
            normalized[sid] = {**payload, "current_value": current_value}
        else:
            try:
                current_value = int(payload)
            except (TypeError, ValueError):
                current_value = 0
            normalized[sid] = {"current_value": current_value}
    return normalized


def _extract_recent_dialogue_history(actor_view: ActorView, max_items: int = 6) -> str:
    raw_messages = actor_view.visible_history or []
    lines: List[str] = []
    for msg in list(raw_messages)[-max_items:]:
        role = str(getattr(msg, "role", "user")).strip().lower()
        content = str(getattr(msg, "content", "")).strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"Player: {content}")
        else:
            lines.append(f"NPC: {content}")
    return "\n".join(lines)


def _get_ability_score(entity: Dict[str, Any], ability: str, default: int = 10) -> int:
    ability_scores = entity.get("ability_scores") or {}
    if isinstance(ability_scores, dict):
        for key, value in ability_scores.items():
            if str(key or "").strip().upper() == ability.upper():
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return default
    return default


def _coerce_count(value: Any, default: int = 1) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _extract_transfer_item_action(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    physical_action = parsed.get("physical_action")
    if isinstance(physical_action, list):
        for action in physical_action:
            if isinstance(action, dict) and str(action.get("action_type", "")).strip().lower() == "transfer_item":
                return action
    if isinstance(physical_action, dict) and str(physical_action.get("action_type", "")).strip().lower() == "transfer_item":
        return physical_action
    return None


def _new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def _build_transfer_item_event(
    *,
    action: Dict[str, Any],
    dialogue_actor_id: str,
    turn_index: int,
) -> Tuple[Optional[DomainEvent], str]:
    source_id = _normalize_entity_id(action.get("source_id"))
    target_id = _normalize_entity_id(action.get("target_id"))
    item_id = _normalize_entity_id(action.get("item_id"))
    transfer_count = _coerce_count(action.get("count", action.get("amount", 1)), default=1)

    if not source_id or not target_id or not item_id or transfer_count <= 0:
        return None, "⚠️ [系统] 物品转移请求无效，缺少 source/target/item/count。"

    reason = "dialogue_transfer_item"
    actor_id = dialogue_actor_id or source_id or "unknown"
    event = DomainEvent(
        event_id=_new_event_id(),
        event_type="actor_item_transaction_requested",
        actor_id=actor_id,
        turn_index=max(0, int(turn_index)),
        visibility="party",
        payload={
            "social_action": {
                "action_type": "item_transfer",
                "actor_id": source_id,
                "target_actor_id": target_id,
                "item_id": item_id,
                "quantity": transfer_count,
                "accepted": True,
                "reason": reason,
            },
            "transaction": {
                "transaction_type": "transfer",
                "from_entity": source_id,
                "to_entity": target_id,
                "item": item_id,
                "quantity": transfer_count,
                "accepted": True,
                "reason": reason,
            },
        },
    )
    return event, ""


def _build_initiative_for_dialogue_combat(
    *,
    entities: Dict[str, Dict[str, Any]],
    target_id: str,
) -> Tuple[List[str], str]:
    target_id = _normalize_entity_id(target_id)
    entries: List[Dict[str, Any]] = []
    for entity_id_raw, entity in entities.items():
        entity_id = _normalize_entity_id(entity_id_raw)
        if not isinstance(entity, dict) or not _is_alive(entity):
            continue
        if not (_is_player_side(entity_id, entity) or entity_id == target_id):
            continue
        raw_roll = random.randint(1, 20)
        dex_mod = calculate_ability_modifier(_get_ability_score(entity, "DEX", 10))
        total = raw_roll + dex_mod
        entries.append(
            {
                "id": entity_id,
                "name": _display_entity_name(entity_id, entity),
                "total": total,
            }
        )
    entries.sort(key=lambda item: item["total"], reverse=True)
    initiative_order = [str(item["id"]) for item in entries]
    order_text = ", ".join(f"{item['name']}({item['total']})" for item in entries)
    return initiative_order, f"⚔️ 战斗开始！先攻顺序：[{order_text}]"


def _build_dialogue_prompt(
    *,
    target_id: str,
    target_entity: Dict[str, Any],
    actor_view: ActorView,
    user_input: str,
) -> str:
    loader = CharacterLoader()
    character_data: Dict[str, Any] = {}
    try:
        character_data = loader.load_character(target_id)
    except Exception:
        character_data = {}

    template_path = str(
        character_data.get("template_path")
        or target_entity.get("template_path")
        or "prompts/hostile_npc_template.j2"
    ).strip()
    template = loader.jinja_env.get_template(template_path)

    attrs_from_yaml = character_data.get("attributes") if isinstance(character_data.get("attributes"), dict) else {}
    actor_name = actor_view.self_state.name or target_entity.get("name", target_id)
    hp = int(actor_view.self_state.hp or target_entity.get("hp", character_data.get("hp", 1)))
    max_hp = int(
        actor_view.self_state.max_hp
        or target_entity.get("max_hp", character_data.get("max_hp", hp))
    )

    merged_attributes = {
        "name": actor_name or character_data.get("name", target_id),
        "race": character_data.get("race", target_entity.get("race", "Unknown")),
        "class": character_data.get("class", target_entity.get("class", "Unknown")),
        "max_hp": max_hp,
        "personality": attrs_from_yaml.get("personality", target_entity.get("personality", {"traits": []})),
        "secret_objective": attrs_from_yaml.get(
            "secret_objective",
            target_entity.get("secret_objective", "Keep your objective hidden."),
        ),
    }

    dynamic_states = _normalize_dynamic_states(actor_view.self_state.dynamic_states)
    if not dynamic_states:
        dynamic_states = _normalize_dynamic_states(
            target_entity.get("dynamic_states")
            or character_data.get("dynamic_states")
            or {}
        )
    if "patience" not in dynamic_states:
        dynamic_states["patience"] = {"current_value": 10}
    if "fear" not in dynamic_states:
        dynamic_states["fear"] = {"current_value": 0}

    registry = get_registry()
    inventory_dict = actor_view.self_state.inventory
    inventory_items = [
        f"{registry.get_name(item_id)} x {int(count)}"
        for item_id, count in inventory_dict.items()
        if str(item_id).strip() and int(count or 0) > 0
    ]

    latest_roll = actor_view.latest_roll if isinstance(actor_view.latest_roll, dict) else {}
    recent_skill_check: Optional[Dict[str, Any]] = None
    if latest_roll:
        result = latest_roll.get("result") if isinstance(latest_roll.get("result"), dict) else {}
        recent_skill_check = {
            "type": str(latest_roll.get("intent", "UNKNOWN")),
            "result": "SUCCESS" if bool(result.get("is_success", False)) else "FAILURE",
            "roll": result.get("total", result.get("raw_roll", "?")),
            "dc": latest_roll.get("dc", "?"),
        }

    rendered = template.render(
        time_of_day=actor_view.time_of_day or "晨曦 (Morning)",
        hp=hp,
        attributes=merged_attributes,
        active_buffs=list(actor_view.self_state.active_buffs or target_entity.get("active_buffs") or []),
        dynamic_states=dynamic_states,
        recent_skill_check=recent_skill_check,
        inventory_items=inventory_items,
    )
    history_text = _extract_recent_dialogue_history(actor_view)
    visible_flags = actor_view.visible_flags
    visible_flags_text = ", ".join(f"{key}={value}" for key, value in sorted(visible_flags.items())) or "None"
    memory_text = "\n".join(actor_view.memory_snippets) if actor_view.memory_snippets else "None"
    peers_text = ", ".join(
        f"{peer.name}({peer.entity_id})"
        for peer in actor_view.other_entities.values()
    ) or "None"
    return (
        f"{rendered}\n\n"
        "[VISIBLE WORLD FLAGS]\n"
        f"{visible_flags_text}\n\n"
        "[VISIBLE COMPANIONS/ENTITIES]\n"
        f"{peers_text}\n\n"
        "[MEMORY SNIPPETS]\n"
        f"{memory_text}\n\n"
        "[RECENT DIALOGUE HISTORY]\n"
        f"{history_text or 'None'}\n\n"
        "[PLAYER LATEST INPUT]\n"
        f"{user_input or '(empty)'}\n"
    )


def dialogue_node(state: GameState) -> Dict[str, Any]:
    intent = str(state.get("intent", "CHAT") or "CHAT").strip().upper()
    entities = copy.deepcopy(state.get("entities") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    pending_events = list(state.get("pending_events") or [])
    intent_context = state.get("intent_context") or {}

    if intent == "START_DIALOGUE":
        target_id = _normalize_entity_id(intent_context.get("action_target"))
        if not target_id or target_id not in entities:
            return {
                "journal_events": [f"❌ [对话] 无法开始交涉：找不到目标 {target_id or 'unknown'}。"],
                "entities": entities,
                "active_dialogue_target": None,
            }
        target = entities.get(target_id, {})
        target_name = _display_entity_name(target_id, target if isinstance(target, dict) else {})
        boss_intro_context = detect_gatekeeper_boss_intro_context(
            {
                **dict(state or {}),
                "entities": entities,
                "target": target_id,
                "intent_context": intent_context if isinstance(intent_context, dict) else {},
            },
            str(state.get("user_input") or ""),
            intent_context if isinstance(intent_context, dict) else {},
        )
        if target_id == "gatekeeper" and boss_intro_context:
            flags = dict(state.get("flags") or {})
            flags["act4_boss_room_entered"] = True
            flags["act4_gatekeeper_confrontation_started"] = True
            flags["act4_poison_valve_intact"] = True
            flags["act4_poison_valve_triggered"] = False
            flags["act4_lab_poison_leak"] = False
            truth_available = bool(boss_intro_context.get("diary_truth_available", False))
            if truth_available:
                flags["act4_diary_truth_available"] = True
            response_text = "不许再靠近！钥匙是我的，门也是我的。主人说过，谁也不能出去。"
            if truth_available:
                response_text = (
                    response_text
                    + " 你读懂的日记让药剂、实验品和钥匙之间的真相成了可以质问他的机会。"
                )
            return {
                "journal_events": [
                    "💬 你走向了 Gatekeeper 准备交涉...",
                    "[Boss Encounter] gatekeeper_confrontation_started",
                ],
                "entities": entities,
                "flags": flags,
                "active_dialogue_target": target_id,
                "speaker_queue": [],
                "current_speaker": target_id,
                "speaker_responses": [(target_id, response_text)],
                "messages": [AIMessage(content=format_history_message(target_id, response_text), name=target_id)],
            }
        return {
            "journal_events": [f"💬 你走向了 {target_name} 准备交涉..."],
            "entities": entities,
            "active_dialogue_target": target_id,
            "speaker_queue": [],
            "current_speaker": target_id,
            "speaker_responses": [],
        }

    if intent != "DIALOGUE_REPLY":
        return {}

    target_id = _normalize_entity_id(
        state.get("active_dialogue_target") or intent_context.get("action_target")
    )
    if not target_id or target_id not in entities:
        return {
            "journal_events": ["❌ [对话] 当前没有有效的交涉目标。"],
            "entities": entities,
            "active_dialogue_target": None,
        }

    target_entity = entities.get(target_id)
    if not isinstance(target_entity, dict):
        return {
            "journal_events": [f"❌ [对话] 无法读取目标 {target_id} 的状态。"],
            "entities": entities,
            "active_dialogue_target": None,
        }

    user_input = str(state.get("user_input", "") or "").strip()
    memory_service = get_default_memory_service()
    actor_view = build_actor_view(
        state,
        target_id,
        memory_provider=ActorScopedMemoryProvider(memory_service.retriever),
    )
    prompt = _build_dialogue_prompt(
        target_id=target_id,
        target_entity=target_entity,
        actor_view=actor_view,
        user_input=user_input,
    )

    try:
        from core.engine import generate_dialogue

        raw_response = _run_blocking_with_timeout(
            generate_dialogue,
            system_prompt=prompt,
            conversation_history=[{"role": "user", "content": user_input or "..."}],
        )
    except Exception:
        raw_response = (
            '{"internal_monologue":"",'
            '"reply":"……",'
            '"trigger_combat":false,'
            '"state_changes":{"patience_delta":0,"fear_delta":0}}'
        )
    parsed = parse_llm_json(str(raw_response or ""))

    npc_name = _display_entity_name(target_id, target_entity)
    reply = str(parsed.get("reply") or "").strip() or "……"
    internal_monologue = str(parsed.get("internal_monologue") or "").strip()
    trigger_combat = bool(parsed.get("trigger_combat", False))
    transfer_action = _extract_transfer_item_action(parsed if isinstance(parsed, dict) else {})
    transfer_events: List[str] = []
    if transfer_action is not None:
        transfer_event, transfer_event_error = _build_transfer_item_event(
            action=transfer_action,
            dialogue_actor_id=target_id,
            turn_index=int(state.get("turn_count") or 0),
        )
        if transfer_event is not None:
            pending_events = append_pending_events(
                dict(state or {}),
                [event_to_dict(transfer_event)],
            )
        elif transfer_event_error:
            transfer_events.append(transfer_event_error)
    state_changes = parsed.get("state_changes") if isinstance(parsed.get("state_changes"), dict) else {}
    patience_delta = int(state_changes.get("patience_delta", 0) or 0)
    fear_delta = int(state_changes.get("fear_delta", 0) or 0)

    dynamic_states = _normalize_dynamic_states(target_entity.get("dynamic_states") or {})
    patience = int(dynamic_states.get("patience", {}).get("current_value", 10))
    fear = int(dynamic_states.get("fear", {}).get("current_value", 0))
    patience = max(0, min(20, patience + patience_delta))
    fear = max(0, min(20, fear + fear_delta))
    dynamic_states.setdefault("patience", {})["current_value"] = patience
    dynamic_states.setdefault("fear", {})["current_value"] = fear
    target_entity["dynamic_states"] = dynamic_states

    dialogue_events = [f'🗣️ [{npc_name}]: "{reply}"']
    if internal_monologue:
        dialogue_events.append(f"🧠 [内心活动] {npc_name}: {internal_monologue}")
    dialogue_events.extend(transfer_events)

    should_break = trigger_combat or patience <= 0
    if should_break:
        target_entity["faction"] = "hostile"
        initiative_order, initiative_log = _build_initiative_for_dialogue_combat(
            entities=entities,
            target_id=target_id,
        )
        dialogue_events.append(f"💢 [谈判破裂] {npc_name} 失去了耐心，准备攻击！")
        if initiative_order:
            dialogue_events.append(initiative_log)
        return {
            "journal_events": dialogue_events,
            "entities": entities,
            "player_inventory": player_inventory if isinstance(player_inventory, dict) else {},
            "pending_events": pending_events,
            "active_dialogue_target": None,
            "speaker_queue": [],
            "current_speaker": target_id,
            "speaker_responses": [(target_id, reply)],
            "messages": [AIMessage(content=format_history_message(target_id, reply), name=target_id)],
            "combat_phase": "IN_COMBAT",
            "combat_active": bool(initiative_order),
            "initiative_order": initiative_order,
            "current_turn_index": 0,
            "turn_resources": {},
        }

    return {
        "journal_events": dialogue_events,
        "entities": entities,
        "player_inventory": player_inventory if isinstance(player_inventory, dict) else {},
        "pending_events": pending_events,
        "active_dialogue_target": target_id,
        "speaker_queue": [],
        "current_speaker": target_id,
        "speaker_responses": [(target_id, reply)],
        "messages": [AIMessage(content=format_history_message(target_id, reply), name=target_id)],
    }
