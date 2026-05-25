"""
Input / World Tick 节点：斜杠命令与世界心跳。
"""

import copy
import json

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from core.graph.graph_state import GameState
from core.campaigns.hazard_lab import detect_secret_study_entry_context
from core.graph.nodes.utils import _entity_snapshot, default_entities, first_entity_id
from core.systems import mechanics
from core.systems.inventory import get_registry

_ACT3_SIDE_MARKERS = (
    "侦察员说得对",
    "顺着侦察员",
    "我同意侦察员",
    "一起嘲笑",
    "和侦察员一起嘲笑",
    "side_with_scout",
    "side with scout",
    "sided with scout",
    "mock gatekeeper",
)
_ACT3_REBUKE_MARKERS = (
    "侦察员，闭嘴",
    "侦察员闭嘴",
    "训斥侦察员",
    "别拱火",
    "别再嘲笑",
    "rebuke_scout",
    "rebuke scout",
    "shut up scout",
)
_READ_DIARY_MARKERS = (
    "读日记",
    "查看日记",
    "阅读日记",
    "hazard_diary",
    "diary",
)
_STUDY_CONTEXT_READ_MARKERS = ("阅读", "调查", "查看", "read", "inspect", "check")
_CHEMICAL_NOTES_MARKERS = ("chemical_notes", "药剂笔记", "化学残页", "化学笔记")
_IRON_KEY_SKETCH_MARKERS = ("iron_key_sketch", "铁钥匙草图", "重铁钥匙草图", "钥匙草图")
_GATEKEEPER_TARGET_MARKERS = ("gatekeeper", "守门人", "训练无人机", "boss")
_GATEKEEPER_NEGOTIATION_MARKERS = ("日记", "药剂", "灵药", "危害", "实验", "解药", "钥匙", "真相")
_GATEKEEPER_ATTACK_MARKERS = ("攻击 gatekeeper", "attack gatekeeper", "攻击守门人", "攻击训练无人机")
_TRAP_DISARM_ACTOR_MARKERS = ("侦察员", "scout")
_TRAP_DISARM_TARGET_MARKERS = ("陷阱", "毒气", "gas_trap_1", "poison_trap", "trap")
_TRAP_DISARM_ACTION_MARKERS = ("解除", "拆", "拆掉", "拆除", "disarm", "disable")
_LAB_DOOR_MARKERS = (
    "door_b_to_d",
    "b-d",
    "bd门",
    "b_d",
    "实验室门",
    "实验室重门",
    "通往实验室",
    "重门",
    "lab door",
    "laboratory door",
)
_LOCKPICK_MARKERS = ("撬锁", "开锁", "撬开", "解锁", "lockpick", "pick the lock", "unlock")
_NEGATIVE_LOCKPICK_MARKERS = (
    "不要撬锁",
    "别撬锁",
    "不撬锁",
    "不要开锁",
    "先别撬",
    "do not lockpick",
    "don't lockpick",
    "without lockpicking",
)
_DOOR_INSPECT_MARKERS = ("检查", "查看", "看看", "观察", "试试", "推门", "开门", "打开", "inspect", "check", "look", "open")
_DOOR_GUIDANCE_QUESTION_MARKERS = (
    "怎么",
    "如何",
    "怎么办",
    "能打开",
    "能不能打开",
    "需要什么",
    "要什么",
    "钥匙在哪",
    "where",
    "how",
)
_GATEKEEPER_MERCY_STANCE_MARKERS = (
    "怎么办",
    "怎么处理",
    "处理他",
    "队友怎么看",
    "该不该",
    "放不放",
    "饶不饶",
    "should we",
)
_GATEKEEPER_MERCY_CHOICE_MARKERS = ("mercy", "spare", "forgive", "放过", "饶了", "饶他", "不杀", "留他一命")
_GATEKEEPER_EXECUTE_CHOICE_MARKERS = ("execute", "kill", "finish him", "处决", "杀了", "解决他", "别留活口")


def _looks_like_act3_choice(map_id: str, user_input: str) -> bool:
    if str(map_id or "").strip().lower() != "hazard_lab":
        return False
    text = str(user_input or "").strip()
    if not text:
        return False
    normalized = text.lower()
    return any(marker in text or marker in normalized for marker in _ACT3_SIDE_MARKERS) or any(
        marker in text or marker in normalized for marker in _ACT3_REBUKE_MARKERS
    )


def _is_hazard_lab_map(map_id: str) -> bool:
    return str(map_id or "").strip().lower() == "hazard_lab"


def _contains_marker(user_input: str, markers: tuple[str, ...]) -> bool:
    text = str(user_input or "").strip()
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in markers)


def _looks_like_read_diary_text(map_id: str, user_input: str) -> bool:
    return _is_hazard_lab_map(map_id) and _contains_marker(user_input, _READ_DIARY_MARKERS)


def _resolve_study_context_read_target(map_id: str, user_input: str) -> str:
    if not _is_hazard_lab_map(map_id):
        return ""
    if not _contains_marker(user_input, _STUDY_CONTEXT_READ_MARKERS):
        return ""
    if _contains_marker(user_input, _IRON_KEY_SKETCH_MARKERS):
        return "iron_key_sketch"
    if _contains_marker(user_input, _CHEMICAL_NOTES_MARKERS):
        return "chemical_notes"
    return ""


def _looks_like_gatekeeper_diary_negotiation(map_id: str, user_input: str) -> bool:
    if not _is_hazard_lab_map(map_id):
        return False
    return _contains_marker(user_input, _GATEKEEPER_TARGET_MARKERS) and _contains_marker(
        user_input,
        _GATEKEEPER_NEGOTIATION_MARKERS,
    )


def _looks_like_explicit_gatekeeper_attack(user_input: str) -> bool:
    return _contains_marker(user_input, _GATEKEEPER_ATTACK_MARKERS)


def _looks_like_scout_trap_disarm(map_id: str, user_input: str) -> bool:
    if not _is_hazard_lab_map(map_id):
        return False
    return (
        _contains_marker(user_input, _TRAP_DISARM_TARGET_MARKERS)
        and _contains_marker(user_input, _TRAP_DISARM_ACTION_MARKERS)
        and (
            _contains_marker(user_input, _TRAP_DISARM_ACTOR_MARKERS)
            or _contains_marker(user_input, ("gas_trap_1", "poison_trap"))
        )
    )


def _looks_like_lab_door_lockpick(map_id: str, user_input: str) -> bool:
    if not _is_hazard_lab_map(map_id):
        return False
    if _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _NEGATIVE_LOCKPICK_MARKERS):
        return False
    return _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _LOCKPICK_MARKERS)


def _looks_like_lab_door_inspect(map_id: str, user_input: str) -> bool:
    if not _is_hazard_lab_map(map_id):
        return False
    if _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _NEGATIVE_LOCKPICK_MARKERS):
        return True
    return (
        _contains_marker(user_input, _LAB_DOOR_MARKERS)
        and _contains_marker(user_input, _DOOR_INSPECT_MARKERS)
        and not _contains_marker(user_input, _DOOR_GUIDANCE_QUESTION_MARKERS)
    )


def _flag_bool(raw_value) -> bool:
    if isinstance(raw_value, dict):
        return bool(raw_value.get("value", False))
    return bool(raw_value)


def _is_gatekeeper_mercy_window_active(state: GameState) -> bool:
    flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
    if _flag_bool(flags.get("hazard_lab_gatekeeper_mercy_resolved")):
        return False
    if (
        _flag_bool(flags.get("hazard_lab_gatekeeper_mercy_window"))
        or _flag_bool(flags.get("hazard_lab_gatekeeper_defeated"))
        or _flag_bool(flags.get("world_hazard_lab_gatekeeper_defeated"))
    ):
        return True
    entities = state.get("entities") if isinstance(state.get("entities"), dict) else {}
    gatekeeper = entities.get("gatekeeper") if isinstance(entities.get("gatekeeper"), dict) else {}
    status = str(gatekeeper.get("status") or "").strip().lower()
    if status in {"defeated", "pleading"}:
        return True
    dynamic_states = gatekeeper.get("dynamic_states") if isinstance(gatekeeper.get("dynamic_states"), dict) else {}
    mercy_state = dynamic_states.get("mercy_window")
    if isinstance(mercy_state, dict):
        return _flag_bool(mercy_state.get("current_value", mercy_state.get("value", False)))
    return _flag_bool(mercy_state)


def _looks_like_gatekeeper_mercy_text(map_id: str, state: GameState, user_input: str) -> bool:
    if not _is_hazard_lab_map(map_id) or not _is_gatekeeper_mercy_window_active(state):
        return False
    return (
        _contains_marker(user_input, _GATEKEEPER_MERCY_STANCE_MARKERS)
        or _contains_marker(user_input, _GATEKEEPER_MERCY_CHOICE_MARKERS)
        or _contains_marker(user_input, _GATEKEEPER_EXECUTE_CHOICE_MARKERS)
    )


def input_node(state: GameState) -> dict:
    """
    处理斜杠命令（/give, /use, /add, /reset 等）。

    解耦原则：直接返回需要修改的字段，不手动合并。
    - player_inventory / entities: 返回完整新 dict，Graph 覆盖
    - journal_events: 返回 [新事件]，merge_events Reducer 自动累加
    """
    user_input = state.get("user_input", "").strip()
    raw_entities = state.get("entities")
    if not raw_entities:
        entities = copy.deepcopy(default_entities)
    else:
        entities = copy.deepcopy(raw_entities)
    # 热更新：仅补齐核心队伍角色，避免将默认敌对单位注入到非目标地图会话。
    for npc_id in ("player", "analyst", "scout", "tactician"):
        if npc_id in entities or npc_id not in default_entities:
            continue
        entities[npc_id] = copy.deepcopy(default_entities[npc_id])
    incoming_intent = str(state.get("intent") or "").strip()
    incoming_intent_key = incoming_intent.lower()
    incoming_target = str(state.get("target") or "").strip()
    incoming_source = str(state.get("source") or "").strip().lower()
    intent_context = {}
    if incoming_target:
        intent_context["action_target"] = incoming_target.lower()
    if incoming_source:
        intent_context["source"] = incoming_source

    base = {
        "intent": "pending",
        "speaker_queue": [],
        "current_speaker": "",
        "speaker_responses": [],
        "is_probing_secret": False,
        "recent_barks": [],
        "turn_count": state.get("turn_count", 0),
        "time_of_day": state.get("time_of_day", "晨曦 (Morning)"),
        "entities": entities,
        "target": incoming_target,
        "source": incoming_source,
        "intent_context": intent_context,
    }

    if not user_input:
        if (
            incoming_intent_key == "interact"
            and incoming_source == "trap_trigger"
            and incoming_target.lower() == "gas_trap_1"
        ):
            intent_context["action_target"] = "gas_trap_1"
            intent_context["source"] = "trap_trigger"
            return {
                **base,
                "intent": "TRIGGER_TRAP",
                "target": "gas_trap_1",
                "source": "trap_trigger",
                "intent_context": intent_context,
            }
        # 保留服务端传入的系统意图（如挂机闲聊），勿覆盖为 pending
        if incoming_intent_key in {"trigger_idle_banter", "init_sync"}:
            return {**base, "intent": incoming_intent}
        return base

    if not user_input.startswith("/"):
        is_read_intent = incoming_intent_key == "read"
        read_target_key = incoming_target.lower()
        read_target_missing = not read_target_key or read_target_key in {"unknown", "null", "none"}
        map_id = str((state.get("map_data") or {}).get("id") or "").strip().lower()
        study_context_target = _resolve_study_context_read_target(map_id, user_input)
        if study_context_target and (read_target_missing or read_target_key in _CHEMICAL_NOTES_MARKERS or read_target_key in _IRON_KEY_SKETCH_MARKERS):
            intent_context["action_target"] = study_context_target
            intent_context["source"] = incoming_source or "act3_study_context"
            return {
                **base,
                "intent": "READ",
                "target": study_context_target,
                "source": incoming_source or "act3_study_context",
                "intent_context": intent_context,
            }
        if is_read_intent and not read_target_missing:
            intent_context.setdefault("action_target", read_target_key)
            intent_context.setdefault("source", incoming_source or "text_input")
            return {
                **base,
                "intent": "READ",
                "target": incoming_target,
                "source": incoming_source or "text_input",
                "intent_context": intent_context,
            }
        if detect_secret_study_entry_context(state, user_input, intent_context):
            intent_context["action_target"] = "cracked_wall"
            intent_context["source"] = "ui_text_normalized"
            return {
                **base,
                "intent": "INTERACT",
                "target": "cracked_wall",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_scout_trap_disarm(map_id, user_input):
            intent_context["action_actor"] = "scout"
            intent_context["action_target"] = "gas_trap_1"
            intent_context["source"] = "ui_text_normalized"
            intent_context["action"] = "disarm_trap"
            return {
                **base,
                "intent": "DISARM",
                "target": "gas_trap_1",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_lab_door_lockpick(map_id, user_input):
            intent_context["action_actor"] = "scout" if _contains_marker(user_input, _TRAP_DISARM_ACTOR_MARKERS) else "player"
            intent_context["action_target"] = "door_b_to_d"
            intent_context["source"] = "ui_text_normalized"
            intent_context["action"] = "lockpick_lab_door"
            return {
                **base,
                "intent": "UNLOCK",
                "target": "door_b_to_d",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_lab_door_inspect(map_id, user_input):
            intent_context["action_target"] = "door_b_to_d"
            intent_context["source"] = "ui_text_normalized"
            intent_context["action"] = "inspect_lab_door"
            return {
                **base,
                "intent": "INTERACT",
                "target": "door_b_to_d",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_gatekeeper_diary_negotiation(map_id, user_input) and not _looks_like_explicit_gatekeeper_attack(user_input):
            intent_context["action_target"] = "gatekeeper"
            intent_context["source"] = "ui_text_normalized"
            intent_context["diary_negotiation_hint"] = True
            return {
                **base,
                "intent": "CHAT",
                "target": "gatekeeper",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_read_diary_text(map_id, user_input) and read_target_missing:
            intent_context["action_target"] = "hazard_diary"
            intent_context["source"] = "ui_text_normalized"
            return {
                **base,
                "intent": "READ",
                "target": "hazard_diary",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if _looks_like_gatekeeper_mercy_text(map_id, state, user_input):
            intent_context["action_target"] = "gatekeeper"
            intent_context["source"] = "ui_text_normalized"
            intent_context["gatekeeper_mercy_hint"] = True
            return {
                **base,
                "intent": "CHAT",
                "target": "gatekeeper",
                "source": "ui_text_normalized",
                "intent_context": intent_context,
            }
        if is_read_intent and read_target_missing:
            if _looks_like_act3_choice(map_id=map_id, user_input=user_input):
                intent_context["action_target"] = "gatekeeper"
                intent_context.setdefault("source", incoming_source or "text_input")
                return {
                    **base,
                    "intent": "CHAT",
                    "target": "gatekeeper",
                    "source": incoming_source or "text_input",
                    "intent_context": intent_context,
                }
            # READ 目标缺失时不保留 READ，避免把后续普通文本误路由到 lore。
            return base
        # 允许前端发送结构化 intent（READ/CHAT/INTERACT 等），避免被覆盖成 pending。
        if incoming_intent and (
            incoming_intent_key not in {"pending", "chat"}
            or bool(incoming_target)
            or bool(incoming_source)
        ):
            return {**base, "intent": incoming_intent}
        return base

    parts = user_input.split()
    command = parts[0].lower()
    player_inv = state.get("player_inventory", {})

    # --- /GIVE <item> [target] ---
    if command == "/give" and len(parts) > 1:
        item_key = parts[1]
        fallback_target = first_entity_id(entities)
        target = parts[2] if len(parts) >= 3 else fallback_target
        if player_inv.get(item_key, 0) > 0 and target in entities:
            new_p = dict(player_inv)
            new_p[item_key] = new_p[item_key] - 1
            if new_p[item_key] <= 0:
                del new_p[item_key]
            new_entities = {}
            for k, v in entities.items():
                new_entities[k] = _entity_snapshot(v)
            new_entities[target]["inventory"][item_key] = new_entities[target]["inventory"].get(item_key, 0) + 1
            new_entities[target]["affection"] = new_entities[target]["affection"] + 2
            response_text = f"[SYSTEM] You gave {item_key} to {target}."
            return {
                "player_inventory": new_p,
                "entities": new_entities,
                "speaker_queue": [],
                "current_speaker": target,
                "speaker_responses": [],
                "journal_events": [f"Player gave {item_key} to {target}."],
                "final_response": response_text,
                "intent": "command_done",
                "is_probing_secret": False,
                "messages": [HumanMessage(content=user_input), AIMessage(content=response_text)],
            }
        response_text = (
            f"[SYSTEM] You don't have {item_key}."
            if player_inv.get(item_key, 0) <= 0
            else f"[SYSTEM] 找不到目标: {target}"
        )
        return {
            "final_response": response_text,
            "intent": "command_done",
            "is_probing_secret": False,
            "messages": [HumanMessage(content=user_input), AIMessage(content=response_text)],
        }

    # --- /ADD <item_id> (开发者指令：刷物品) ---
    if command == "/add" and len(parts) >= 2:
        item_id = parts[1]
        new_p = dict(player_inv)
        new_p[item_id] = new_p.get(item_id, 0) + 1
        return {
            "player_inventory": new_p,
            "intent": "dev_command",
            "final_response": f"[SYSTEM] DevMode: 获得了 {item_id}。",
            "is_probing_secret": False,
        }

    # --- /RESET (开发者指令：世界重置) ---
    if command == "/reset":
        # Reset must rebuild the exact same full state shape as a fresh save.
        # Partial resets leave combat resources and player entities stale in checkpoints.
        from core.systems.world_init import get_initial_world_state

        current_map_id = str((state.get("map_data") or {}).get("id") or "").strip()
        fresh_state = get_initial_world_state(map_id=current_map_id or "training_range")
        current_messages = state.get("messages", [])
        delete_msgs = []
        for m in current_messages:
            mid = m.get("id") if isinstance(m, dict) else (getattr(m, "id", None) if hasattr(m, "id") else None)
            if mid:
                delete_msgs.append(RemoveMessage(id=mid))
        if not delete_msgs:
            messages_update = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
        else:
            messages_update = delete_msgs
        return {
            **fresh_state,
            "messages": messages_update,
            "speaker_queue": [],
            "current_speaker": "",
            "speaker_responses": [],
            "intent_context": {},
            "latest_roll": {},
            "intent": "dev_command",
            "final_response": "[SYSTEM] 🌍 世界线已重置 (World Reset)。实体状态与历史记忆已全部归零。",
            "is_probing_secret": False,
        }

    # --- /WAIT (等待一回合) ---
    if command == "/wait":
        return {
            "intent": "system_wait",
            "final_response": "⏳ 时间流逝……周围静悄悄的。",
            "is_probing_secret": False,
        }

    # --- /BUFF <target> <status_id> <duration> <value> (开发者指令：加状态) ---
    if command == "/buff" and len(parts) >= 5:
        target = parts[1]
        buff_id = parts[2]
        duration = int(parts[3])
        value = int(parts[4])

        raw = state.get("entities") or entities
        entities_copy = {k: _entity_snapshot(v) for k, v in entities.items()}
        for k, v in raw.items():
            if k not in entities_copy:
                entities_copy[k] = {"hp": 20, "active_buffs": [], "affection": 0, "inventory": {}}
            entities_copy[k].update(_entity_snapshot(v))
        if target in entities_copy:
            new_buffs = list(entities_copy[target].get("active_buffs", []))
            new_buffs.append({"id": buff_id, "duration": duration, "value": value})
            entities_copy[target]["active_buffs"] = new_buffs
            response_text = f"[SYSTEM] DevMode: 给 {target} 施加状态 '{buff_id}'，持续 {duration} 回合。"
        else:
            response_text = f"[SYSTEM] 找不到目标实体: {target}"

        return {
            "entities": entities_copy,
            "intent": "dev_command",
            "final_response": response_text,
            "is_probing_secret": False,
        }

    # --- /USE <item_id> [target] (玩家动作：使用物品) ---
    if command == "/use" and len(parts) >= 2:
        item_id = parts[1]
        target = parts[2] if len(parts) >= 3 else "player"

        if player_inv.get(item_id, 0) <= 0:
            return {
                "intent": "command_failed",
                "final_response": f"[SYSTEM] 你的背包里没有 '{item_id}'。",
                "is_probing_secret": False,
            }

        new_p = dict(player_inv)
        new_p[item_id] = new_p[item_id] - 1
        if new_p[item_id] <= 0:
            del new_p[item_id]

        if item_id == "healing_potion":
            raw = state.get("entities") or entities
            entities_copy = {k: _entity_snapshot(v) for k, v in entities.items()}
            for k, v in raw.items():
                if k not in entities_copy:
                    entities_copy[k] = {"hp": 20, "active_buffs": [], "affection": 0, "inventory": {}}
                entities_copy[k].update(_entity_snapshot(v))
            if target == "player":
                return {
                    "player_inventory": new_p,
                    "intent": "command_done",
                    "messages": [HumanMessage(content="*你喝下了一瓶治疗药水。*")],
                    "final_response": "",
                    "is_probing_secret": False,
                }
            if target in entities_copy:
                current_hp = int(entities_copy[target].get("hp", 20) or 0)
                max_hp = int(entities_copy[target].get("max_hp", current_hp or 20) or 20)
                entities_copy[target]["hp"] = min(max_hp, current_hp + 10)
                entities_copy[target]["max_hp"] = max_hp
                action_msg = f"*你强行掰开 {target} 的嘴，灌下了治疗药水。生命值恢复了。*"
                return {
                    "entities": entities_copy,
                    "player_inventory": new_p,
                    "speaker_queue": [],
                    "current_speaker": target,
                    "speaker_responses": [],
                    "intent": "command_done",
                    "messages": [HumanMessage(content=action_msg)],
                    "final_response": "",
                    "is_probing_secret": False,
                }
            response_text = f"[SYSTEM] 找不到目标实体: {target}"
            return {
                "player_inventory": player_inv,
                "intent": "command_failed",
                "final_response": response_text,
                "is_probing_secret": False,
            }

        item_data = get_registry().get(item_id)
        effect = mechanics.apply_item_effect(item_id, item_data)
        focus_speaker = (state.get("current_speaker") or "").strip() or first_entity_id(entities)
        return {
            "player_inventory": new_p,
            "speaker_queue": [],
            "current_speaker": focus_speaker,
            "speaker_responses": [],
            "journal_events": [f"Player used {item_id}: {effect['message']}"],
            "final_response": f"[SYSTEM] You used {item_id}: {effect['message']}",
            "intent": "command_done",
            "is_probing_secret": False,
            "messages": [
                HumanMessage(content=user_input),
                AIMessage(content=f"[SYSTEM] You used {item_id}: {effect['message']}"),
            ],
        }

    # --- /FLAG <key> <value> (开发者指令：动态修改标志位) ---
    if command == "/flag" and len(parts) > 2:
        flag_key = parts[1]
        raw_flag_value = user_input.split(None, 2)[2]
        flag_val: object
        try:
            flag_val = json.loads(raw_flag_value)
        except json.JSONDecodeError:
            flag_val_str = parts[2].lower()
            flag_val = True if flag_val_str in ("true", "1", "yes", "on") else False

        new_flags = dict(state.get("flags", {}))
        new_flags[flag_key] = flag_val

        response_text = f"[SYSTEM] DevMode: Flag '{flag_key}' set to {flag_val}."
        return {
            "flags": new_flags,
            "final_response": response_text,
            "intent": "command_done",
            "is_probing_secret": False,
            "messages": [HumanMessage(content=user_input), AIMessage(content=response_text)],
        }

    response_text = "[SYSTEM] Unknown command."
    return {
        "final_response": response_text,
        "intent": "command_done",
        "is_probing_secret": False,
        "messages": [HumanMessage(content=user_input), AIMessage(content=response_text)],
    }


def world_tick_node(state: dict) -> dict:
    """世界心跳节点：推进回合数，遍历所有实体结算状态效果"""
    from ui.renderer import GameRenderer

    ui = GameRenderer()
    current_turn = state.get("turn_count", 0) + 1

    time_cycles = ["晨曦 (Morning)", "正午 (Noon)", "黄昏 (Dusk)", "深夜 (Night)"]
    new_time = time_cycles[(current_turn // 3) % 4]
    ui.print_system_info(f"⏳ [World Tick] 回合推进至 {current_turn} | 当前时间: {new_time}")

    entities_in = state.get("entities") or copy.deepcopy(default_entities)
    entities_out = {}

    for entity_id, entity_data in entities_in.items():
        entity_data = dict(entity_data)
        current_hp = entity_data.get("hp", 20)
        buffs = list(entity_data.get("active_buffs", []))
        surviving_buffs = []

        for buff in buffs:
            b_id = buff["id"]
            b_val = buff.get("value", 0)

            if b_id in ["poisoned", "burning", "bleeding"]:
                old_hp = current_hp
                current_hp = max(0, current_hp - b_val)
                actual_damage = old_hp - current_hp
                if actual_damage > 0:
                    ui.print_system_info(
                        f"🩸 [Status] {entity_id} 因 {b_id} 受到 {actual_damage} 点伤害！剩余 HP: {current_hp}"
                    )
            elif b_id in ["regeneration"]:
                current_hp = min(20, current_hp + b_val)
                ui.print_system_info(f"✨ [Status] {entity_id} 因 {b_id} 恢复 {b_val} 点生命！剩余 HP: {current_hp}")

            buff["duration"] -= 1
            if buff["duration"] > 0:
                surviving_buffs.append(buff)
            else:
                ui.print_system_info(f"💨 [Status] {entity_id} 的 {b_id} 状态已解除。")

        entity_data["hp"] = max(0, current_hp)
        entity_data["active_buffs"] = surviving_buffs
        entity_data.setdefault("affection", 0)
        entity_data.setdefault("inventory", {})
        entity_data.setdefault("position", default_entities.get(entity_id, {}).get("position", "camp_center"))
        if isinstance(entity_data.get("inventory"), list):
            entity_data["inventory"] = {
                x.get("id", ""): x.get("count", 0) for x in entity_data["inventory"] if x.get("id")
            }
        entities_out[entity_id] = entity_data

    return {
        "turn_count": current_turn,
        "time_of_day": new_time,
        "entities": entities_out,
    }
