"""
DM 分析、多人发言推进、旁白节点。
"""

import asyncio
import copy
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional

from langchain_core.messages import AIMessage

from core.campaigns import (
    ACT3_CHOICE_REBUKE_SCOUT,
    ACT3_CHOICE_SIDE_WITH_SCOUT,
    ACT4_POST_COMBAT_BANTER,
    detect_scout_memory_echo_context,
    detect_diary_negotiation_context,
    detect_gatekeeper_boss_intro_context,
    detect_gatekeeper_boss_resolution_context,
    detect_gatekeeper_boss_strategy_context,
    detect_gatekeeper_mercy_context,
    detect_key_guidance_context,
    detect_lab_act3_choice,
    detect_lab_act4_post_combat_banter,
    detect_secret_study_entry_context,
    detect_secret_study_observation_context,
    detect_study_chest_loot_context,
    detect_trap_awareness_context,
)
from core.engine import generate_dialogue, parse_ai_response
from core.graph.graph_state import GameState
from core.graph.nodes.utils import _build_item_lore, default_entities, entity_display_name, first_entity_id
from core.llm.dm import analyze_intent
from core.utils.text_processor import format_history_message

LLM_TIMEOUT_SECONDS = 4.5
_DOOR_ATTACK_MARKERS = ("攻击门", "砸门", "打门", "破门", "attack door", "smash door")
_DOOR_INTERACT_MARKERS = (
    "开门",
    "打开门",
    "使用钥匙",
    "用钥匙开门",
    "heavy_iron_key",
    "heavy_oak_door_1",
    "打开 heavy_oak_door_1",
    "check heavy_oak_door_1",
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


def _has_scripted_hazard_reason(analysis: dict) -> bool:
    reason = str((analysis or {}).get("reason") or "").strip()
    return reason.startswith(("scripted_hazard_lab_", "scripted_diary_negotiation_"))


def _looks_like_gatekeeper_boss_room_intro_text(user_input: str) -> bool:
    return _contains_marker(
        user_input,
        (
            "进入实验室",
            "进入实验室房间",
            "进入 boss房",
            "进入boss房",
            "boss房",
            "boss room",
            "laboratory",
            "lab",
        ),
    )


def _apply_act3_hazard_lab_override(*, state: GameState, analysis: dict) -> dict:
    choice = detect_lab_act3_choice(dict(state or {}))
    if choice not in {ACT3_CHOICE_SIDE_WITH_SCOUT, ACT3_CHOICE_REBUKE_SCOUT}:
        return analysis

    reason = (
        "act3_side_with_scout"
        if choice == ACT3_CHOICE_SIDE_WITH_SCOUT
        else "act3_rebuke_scout"
    )
    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "gatekeeper"
    overridden["reason"] = reason
    overridden["responders"] = ["scout", "analyst"]

    flags_changed = dict(overridden.get("flags_changed") or {})
    flags_changed["hazard_lab_gatekeeper_negotiation_started"] = True
    flags_changed["hazard_lab_scout_mocked_gatekeeper"] = True
    flags_changed["hazard_lab_player_sided_with_scout"] = (
        choice == ACT3_CHOICE_SIDE_WITH_SCOUT
    )
    flags_changed["hazard_lab_gatekeeper_combat_triggered"] = True
    overridden["flags_changed"] = flags_changed

    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["act3_choice"] = choice
    overridden["intent_context"] = intent_context
    return overridden


def _apply_act4_hazard_lab_override(*, state: GameState, analysis: dict) -> dict:
    if not detect_lab_act4_post_combat_banter(dict(state or {})):
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "heavy_oak_door_1"
    overridden["reason"] = ACT4_POST_COMBAT_BANTER
    overridden["responders"] = ["scout", "analyst", "tactician"]

    flags_changed = dict(overridden.get("flags_changed") or {})
    flags_changed["hazard_lab_post_combat_banter_done"] = True
    overridden["flags_changed"] = flags_changed

    current_flags = dict(state.get("flags") or {})
    sided_with_scout = bool(current_flags.get("hazard_lab_player_sided_with_scout", False))
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["act4_post_combat_banter"] = True
    intent_context["player_sided_with_scout"] = sided_with_scout
    overridden["intent_context"] = intent_context
    return overridden


def _apply_gatekeeper_boss_intro_override(*, state: GameState, analysis: dict) -> dict:
    if _has_scripted_hazard_reason(analysis):
        return analysis
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    if not str((analysis or {}).get("action_type") or "").strip():
        # Let plain talk-to-Gatekeeper turns reach DM analysis; the dialogue node
        # still starts the Act 4 intro when DM chooses START_DIALOGUE.
        if not _looks_like_gatekeeper_boss_room_intro_text(str(state.get("user_input") or "")):
            return analysis
    if str(state.get("active_dialogue_target") or "").strip().lower() == "gatekeeper":
        return analysis
    user_input = str(state.get("user_input") or "")
    state_target = str(state.get("target") or "").strip().lower()
    if state_target == "gatekeeper" and not _contains_marker(
        user_input,
        ("谈谈", "靠近", "进入实验室", "boss", "boss房", "laboratory", "lab"),
    ):
        return analysis
    existing_context = (analysis or {}).get("intent_context")
    if isinstance(existing_context, dict) and existing_context.get("diary_negotiation_context"):
        return analysis

    context = detect_gatekeeper_boss_intro_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "START_DIALOGUE"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "gatekeeper"
    overridden["reason"] = "act4_gatekeeper_boss_intro"
    overridden["responders"] = ["gatekeeper"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["gatekeeper_boss_intro_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_gatekeeper_boss_strategy_override(*, state: GameState, analysis: dict) -> dict:
    if _has_scripted_hazard_reason(analysis):
        return analysis
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    context = detect_gatekeeper_boss_strategy_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis
    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "gatekeeper"
    overridden["reason"] = "act4_gatekeeper_boss_strategy"
    overridden["responders"] = ["scout", "analyst", "tactician"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["gatekeeper_boss_strategy_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_gatekeeper_boss_resolution_override(*, state: GameState, analysis: dict) -> dict:
    if _has_scripted_hazard_reason(analysis):
        return analysis
    context = detect_gatekeeper_boss_resolution_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis

    route = str(context.get("route") or "").strip()
    actor = "player"
    target = "gatekeeper"
    if route in {"scout_steal", "disarm_poison_valve"}:
        actor = "scout"
    elif route == "assault":
        actor = "tactician"
    if route == "disarm_poison_valve":
        target = "poison_valve"

    overridden = dict(analysis or {})
    overridden["action_type"] = "ACTION"
    overridden["action_actor"] = actor
    overridden["action_target"] = target
    overridden["difficulty_class"] = 12
    overridden["reason"] = f"act4_gatekeeper_boss_{route}"
    overridden["responders"] = []
    intent_context = dict(overridden.get("intent_context") or {})
    if route == "truth_negotiation":
        intent_context.pop("diary_negotiation_context", None)
        intent_context.pop("diary_negotiation_hint", None)
    intent_context["gatekeeper_boss_resolution_context"] = context
    intent_context["action_actor"] = actor
    intent_context["action_target"] = target
    overridden["intent_context"] = intent_context
    return overridden


def _apply_key_guidance_override(*, state: GameState, analysis: dict) -> dict:
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    context = detect_key_guidance_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        "",
    )
    if not context:
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "door_b_to_d"
    overridden["reason"] = "key_aware_companion_guidance"
    overridden["responders"] = ["scout", "analyst", "tactician"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["key_guidance_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_diary_negotiation_override(*, state: GameState, analysis: dict) -> dict:
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    existing_context = (analysis or {}).get("intent_context")
    if isinstance(existing_context, dict) and existing_context.get("gatekeeper_boss_intro_context"):
        return analysis
    if isinstance(existing_context, dict) and existing_context.get("gatekeeper_boss_resolution_context"):
        return analysis
    context = detect_diary_negotiation_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
    )
    if not context or not bool(context.get("decoded_diary", False)):
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "gatekeeper"
    overridden["reason"] = "diary_evidence_pressure"
    overridden["responders"] = ["gatekeeper", "analyst"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["diary_negotiation_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _looks_like_scout_trap_disarm(user_input: str) -> bool:
    return (
        _contains_marker(user_input, _TRAP_DISARM_TARGET_MARKERS)
        and _contains_marker(user_input, _TRAP_DISARM_ACTION_MARKERS)
        and (
            _contains_marker(user_input, _TRAP_DISARM_ACTOR_MARKERS)
            or _contains_marker(user_input, ("gas_trap_1", "poison_trap"))
        )
    )


def _apply_trap_disarm_override(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis
    if not _looks_like_scout_trap_disarm(str(state.get("user_input") or "")):
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "DISARM"
    overridden["action_actor"] = "scout"
    overridden["action_target"] = "gas_trap_1"
    overridden["reason"] = "hazard_lab_scout_trap_disarm"
    overridden["responders"] = []
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["action"] = "disarm_trap"
    overridden["intent_context"] = intent_context
    return overridden


def _apply_trap_awareness_override(*, state: GameState, analysis: dict) -> dict:
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    action_target = str((analysis or {}).get("action_target") or "").strip().lower()
    analysis_context = (analysis or {}).get("intent_context")
    if action_target == "gatekeeper":
        return analysis
    if isinstance(analysis_context, dict) and analysis_context.get("diary_negotiation_context"):
        return analysis
    source = str(state.get("source") or "").strip().lower()
    if isinstance(analysis_context, dict):
        source = str(analysis_context.get("source") or source).strip().lower()
    if existing_action == "INTERACT" and source == "trap_trigger":
        return analysis
    context = detect_trap_awareness_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis
    if not bool(context.get("can_detect", False)):
        return analysis
    if bool(context.get("revealed", False)) or bool(context.get("disarmed", False)) or bool(context.get("triggered", False)):
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "gas_trap_1"
    overridden["reason"] = "hazard_lab_trap_awareness"
    overridden["responders"] = ["scout"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["trap_awareness_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_secret_study_entry_override(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    context = detect_secret_study_entry_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "INTERACT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = str(context.get("target_id") or "cracked_wall")
    overridden["reason"] = "secret_study_discovery"
    overridden["responders"] = []
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["secret_study_entry_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_secret_study_observation_override(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis
    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis
    context = detect_secret_study_observation_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        (analysis or {}).get("intent_context") if isinstance((analysis or {}).get("intent_context"), dict) else {},
    )
    if not context:
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "CHAT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = "room_c_secret_study"
    overridden["reason"] = "secret_study_companion_observation"
    overridden["responders"] = ["scout", "analyst", "tactician"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["secret_study_observation_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _apply_scout_memory_echo_override(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis

    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis

    out = dict(analysis or {})
    intent_context = dict(out.get("intent_context") or {})
    action_target = str(out.get("action_target") or "").strip().lower()
    action_actor = str(out.get("action_actor") or "").strip().lower()
    responders = [
        str(item).strip().lower()
        for item in (out.get("responders") or [])
        if str(item or "").strip()
    ]
    helper_context = {
        **intent_context,
        "action_target": action_target,
        "action_actor": action_actor,
        "responders": responders,
    }
    context = detect_scout_memory_echo_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        helper_context,
    )
    if not context:
        return analysis

    out["action_type"] = "CHAT"
    out["action_actor"] = "player"
    out["reason"] = "scout_memory_echo"
    if not action_target or action_target in {"unknown", "none", "null"}:
        out["action_target"] = "scout"
    if "scout" not in responders:
        responders = ["scout"] + [item for item in responders if item != "scout"]
    out["responders"] = responders or ["scout"]
    intent_context["memory_echo_context"] = context
    out["intent_context"] = intent_context
    return out


def _apply_gatekeeper_mercy_override(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis

    existing_action = str((analysis or {}).get("action_type") or "").strip().upper()
    if existing_action in {"DISARM", "LOOT", "READ", "INTERACT", "MOVE", "APPROACH", "TRIGGER_TRAP", "ATTACK", "CAST_SPELL", "UNLOCK"}:
        return analysis

    out = dict(analysis or {})
    intent_context = dict(out.get("intent_context") or {})
    action_target = str(out.get("action_target") or "").strip().lower()
    helper_context = {
        **intent_context,
        "action_target": action_target or str(state.get("target") or "").strip().lower(),
    }
    context = detect_gatekeeper_mercy_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
        helper_context,
    )
    if not context:
        return analysis

    phase = str(context.get("phase") or "stance").strip()
    out["action_type"] = "CHAT"
    out["action_actor"] = "player"
    out["action_target"] = "gatekeeper"
    out["reason"] = "gatekeeper_mercy_resolution" if phase == "resolution" else "gatekeeper_mercy_stance"
    out["responders"] = ["analyst"] if phase == "resolution" else ["analyst", "tactician", "scout"]
    intent_context["gatekeeper_mercy_context"] = context
    out["intent_context"] = intent_context
    return out


def _contains_marker(user_input: str, markers: tuple[str, ...]) -> bool:
    text = str(user_input or "").strip()
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in markers)


def _looks_like_read_diary_text(user_input: str) -> bool:
    return _contains_marker(user_input, _READ_DIARY_MARKERS)


def _resolve_study_context_read_target(user_input: str) -> str:
    if not _contains_marker(user_input, _STUDY_CONTEXT_READ_MARKERS):
        return ""
    if _contains_marker(user_input, _IRON_KEY_SKETCH_MARKERS):
        return "iron_key_sketch"
    if _contains_marker(user_input, _CHEMICAL_NOTES_MARKERS):
        return "chemical_notes"
    return ""


def _looks_like_gatekeeper_diary_negotiation(user_input: str) -> bool:
    return _contains_marker(user_input, _GATEKEEPER_TARGET_MARKERS) and _contains_marker(
        user_input,
        _GATEKEEPER_NEGOTIATION_MARKERS,
    )


def _looks_like_explicit_gatekeeper_attack(user_input: str) -> bool:
    return _contains_marker(user_input, _GATEKEEPER_ATTACK_MARKERS)


def _looks_like_lab_door_lockpick(user_input: str) -> bool:
    if _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _NEGATIVE_LOCKPICK_MARKERS):
        return False
    return _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _LOCKPICK_MARKERS)


def _looks_like_lab_door_inspect(user_input: str) -> bool:
    if _contains_marker(user_input, _LAB_DOOR_MARKERS) and _contains_marker(user_input, _NEGATIVE_LOCKPICK_MARKERS):
        return True
    return (
        _contains_marker(user_input, _LAB_DOOR_MARKERS)
        and _contains_marker(user_input, _DOOR_INSPECT_MARKERS)
        and not _contains_marker(user_input, _DOOR_GUIDANCE_QUESTION_MARKERS)
    )


def _apply_hazard_lab_ui_text_fallback(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis

    user_input = str(state.get("user_input") or "")
    out = dict(analysis or {})
    incoming_target = str(state.get("target") or "").strip().lower()
    intent_context = out.get("intent_context") if isinstance(out.get("intent_context"), dict) else {}
    action_target = str(out.get("action_target") or incoming_target or "").strip().lower()
    target_missing = not action_target or action_target in {"unknown", "null", "none"}
    if str(out.get("action_type") or "").strip().upper() == "READ" and not target_missing:
        return analysis

    study_context_target = _resolve_study_context_read_target(user_input)
    if study_context_target and (target_missing or action_target in set(_CHEMICAL_NOTES_MARKERS) or action_target in set(_IRON_KEY_SKETCH_MARKERS)):
        out["action_type"] = "READ"
        out["action_actor"] = "player"
        out["action_target"] = study_context_target
        out["reason"] = "ui_text_act3_study_context_read"
        out["responders"] = []
        intent_context = dict(intent_context)
        intent_context["source"] = "act3_study_context"
        out["intent_context"] = intent_context
        return out

    if _looks_like_read_diary_text(user_input) and target_missing:
        out["action_type"] = "READ"
        out["action_actor"] = "player"
        out["action_target"] = "hazard_diary"
        out["reason"] = "ui_text_read_hazard_diary"
        out["responders"] = []
        intent_context = dict(intent_context)
        intent_context["source"] = "ui_text_normalized"
        out["intent_context"] = intent_context
        return out

    if _looks_like_lab_door_lockpick(user_input):
        out["action_type"] = "UNLOCK"
        out["action_actor"] = "scout" if _contains_marker(user_input, _TRAP_DISARM_ACTOR_MARKERS) else "player"
        out["action_target"] = "door_b_to_d"
        out["reason"] = "ui_text_lab_door_lockpick"
        out["responders"] = []
        intent_context = dict(intent_context)
        intent_context["source"] = "ui_text_normalized"
        intent_context["action"] = "lockpick_lab_door"
        out["intent_context"] = intent_context
        return out

    if _looks_like_lab_door_inspect(user_input):
        out["action_type"] = "INTERACT"
        out["action_actor"] = "player"
        out["action_target"] = "door_b_to_d"
        out["reason"] = "ui_text_lab_door_inspect"
        out["responders"] = []
        intent_context = dict(intent_context)
        intent_context["source"] = "ui_text_normalized"
        intent_context["action"] = "inspect_lab_door"
        out["intent_context"] = intent_context
        return out

    if _looks_like_explicit_gatekeeper_attack(user_input):
        return out
    if _contains_marker(
        user_input,
        (
            "进入实验室",
            "和 gatekeeper 谈谈",
            "和gatekeeper谈谈",
            "和守门人谈谈",
            "和守门人谈谈",
            "靠近 gatekeeper",
            "靠近守门人",
            "靠近守门人",
            "boss房",
            "boss room",
        ),
    ):
        return out
    if not _looks_like_gatekeeper_diary_negotiation(user_input):
        return out

    patched_state = {
        **dict(state or {}),
        "target": "gatekeeper",
        "intent_context": {
            **(state.get("intent_context") if isinstance(state.get("intent_context"), dict) else {}),
            "action_target": "gatekeeper",
        },
    }
    context = detect_diary_negotiation_context(patched_state, user_input)

    out["action_type"] = "CHAT"
    out["action_actor"] = "player"
    out["action_target"] = "gatekeeper"
    out["reason"] = "diary_evidence_pressure" if context and bool(context.get("decoded_diary", False)) else "ui_text_gatekeeper_diary_negotiation"
    out["responders"] = ["gatekeeper", "analyst"] if context and bool(context.get("decoded_diary", False)) else ["gatekeeper"]
    intent_context = dict(intent_context)
    intent_context["source"] = "ui_text_normalized"
    intent_context["diary_negotiation_hint"] = True
    if context and bool(context.get("decoded_diary", False)):
        intent_context["diary_negotiation_context"] = context
    out["intent_context"] = intent_context
    return out


def _apply_study_chest_loot_override(*, state: GameState, analysis: dict) -> dict:
    context = detect_study_chest_loot_context(
        dict(state or {}),
        str(state.get("user_input") or ""),
    )
    if not context:
        return analysis

    overridden = dict(analysis or {})
    overridden["action_type"] = "LOOT"
    overridden["action_actor"] = "player"
    overridden["action_target"] = str(context.get("target_id") or "chest_1")
    overridden["reason"] = "hazard_lab_study_chest_loot"
    overridden["responders"] = ["player"]
    intent_context = dict(overridden.get("intent_context") or {})
    intent_context["study_chest_loot_context"] = context
    overridden["intent_context"] = intent_context
    return overridden


def _run_blocking_with_timeout(func, *args, timeout: float = LLM_TIMEOUT_SECONDS, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            raise TimeoutError("LLM call timeout") from exc


def _coerce_client_intent(state: GameState) -> str:
    raw_intent = str(state.get("intent") or "").strip().upper()
    if raw_intent in {"READ", "INTERACT", "CHAT", "START_DIALOGUE", "DIALOGUE_REPLY", "DISARM", "MOVE", "APPROACH", "TRIGGER_TRAP"}:
        return raw_intent
    if raw_intent == "UI_ACTION_LOOT":
        return "LOOT"
    return ""


def _extract_client_target(state: GameState, *, include_active_dialogue_target: bool) -> str:
    intent_context = state.get("intent_context") if isinstance(state, dict) else {}
    ctx = intent_context if isinstance(intent_context, dict) else {}
    active_dialogue_target = state.get("active_dialogue_target") if include_active_dialogue_target else ""
    return (
        str(
            state.get("target")
            or ctx.get("action_target")
            or active_dialogue_target
            or ""
        )
        .strip()
        .lower()
    )


def _is_hazard_lab(state: GameState) -> bool:
    map_id = str((state.get("map_data") or {}).get("id") or "").strip().lower()
    return map_id == "hazard_lab"


def _first_non_player_speaker(entities: dict, fallback_speaker: str) -> str:
    fallback = str(fallback_speaker or "").strip().lower()
    fallback_entity = entities.get(fallback) if isinstance(entities, dict) else None
    if fallback and _is_valid_responder_candidate(fallback, fallback_entity):
        return fallback
    for entity_id, entity in entities.items():
        normalized = str(entity_id or "").strip().lower()
        if _is_valid_responder_candidate(normalized, entity):
            return normalized
    return ""


def _is_valid_responder_candidate(entity_id: str, entity: Optional[dict]) -> bool:
    normalized_id = str(entity_id or "").strip().lower()
    if not normalized_id or normalized_id in {"player", "unknown"}:
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


def _looks_like_door_attack(user_input: str) -> bool:
    text = str(user_input or "").strip()
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in _DOOR_ATTACK_MARKERS)


def _looks_like_door_interact(user_input: str) -> bool:
    text = str(user_input or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if _looks_like_door_attack(text):
        return False
    has_door_hint = ("门" in text) or ("door" in lowered) or ("heavy_oak_door_1" in lowered)
    if not has_door_hint:
        return False
    return any(marker in text or marker in lowered for marker in _DOOR_INTERACT_MARKERS)


def _apply_hazard_door_target_fallback(*, state: GameState, analysis: dict) -> dict:
    if not _is_hazard_lab(state):
        return analysis
    out = dict(analysis or {})
    intent_context = out.get("intent_context") if isinstance(out.get("intent_context"), dict) else {}
    if intent_context.get("key_guidance_context"):
        return out
    user_input = str(state.get("user_input") or "")
    if _looks_like_door_attack(user_input):
        return out
    if not _looks_like_door_interact(user_input):
        return out

    action_type = str(out.get("action_type") or "").strip().upper()
    action_target = str(out.get("action_target") or "").strip().lower()
    if action_type in {"", "CHAT", "INTERACT", "START_DIALOGUE", "DIALOGUE_REPLY"}:
        out["action_type"] = "INTERACT"
    if not action_target:
        out["action_target"] = "heavy_oak_door_1"
    return out


def _build_structured_client_analysis(
    *,
    state: GameState,
    entities: dict,
    fallback_speaker: str,
) -> Optional[dict]:
    client_intent = _coerce_client_intent(state)
    if not client_intent:
        return None

    # Only treat CHAT as structured when the client provides explicit routing hints.
    # Otherwise we should keep natural-language turns on DM intent analysis.
    if client_intent == "CHAT":
        has_explicit_hints = bool(
            str(state.get("target") or "").strip()
            or str(state.get("source") or "").strip()
        )
        if not has_explicit_hints:
            return None

    include_active_dialogue_target = client_intent not in {"READ", "INTERACT"}
    action_target = _extract_client_target(
        state,
        include_active_dialogue_target=include_active_dialogue_target,
    )
    action_type = client_intent
    source = str(state.get("source") or "").strip().lower()
    active_dialogue_target = str(state.get("active_dialogue_target") or "").strip().lower()
    if client_intent == "CHAT" and action_target == "gatekeeper":
        action_type = "DIALOGUE_REPLY" if active_dialogue_target == "gatekeeper" else "START_DIALOGUE"
    if client_intent == "INTERACT" and source == "trap_trigger" and action_target == "gas_trap_1":
        action_type = "TRIGGER_TRAP"

    if _is_hazard_lab(state) and action_target == "heavy_oak_door_1":
        if not _looks_like_door_attack(str(state.get("user_input") or "")):
            action_type = "INTERACT"
    elif _is_hazard_lab(state) and client_intent == "INTERACT" and not action_target:
        if _looks_like_door_interact(str(state.get("user_input") or "")) and not _looks_like_door_attack(
            str(state.get("user_input") or "")
        ):
            action_target = "heavy_oak_door_1"

    responders = []
    structured_fallback_speaker = _first_non_player_speaker(entities, fallback_speaker)
    if (
        action_target
        and action_target in entities
        and _is_valid_responder_candidate(action_target, entities.get(action_target))
    ):
        responders = [action_target]
    elif structured_fallback_speaker:
        responders = [structured_fallback_speaker]

    return {
        "action_type": action_type,
        "difficulty_class": 12,
        "reason": "client_structured_intent",
        "is_probing_secret": False,
        "responders": responders,
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": "player",
        "action_target": action_target,
        "intent_context": {
            "source": source,
        },
    }


def _is_idle_banter_speaker(entity_id: str, entity: dict) -> bool:
    normalized_id = str(entity_id or "").strip().lower()
    if not normalized_id or normalized_id in {"player", "unknown"}:
        return False
    faction = str(entity.get("faction", "")).strip().lower()
    if faction in {"hostile", "neutral"}:
        return False
    status = str(entity.get("status", "alive")).strip().lower()
    if status in {"dead", "downed", "unconscious"}:
        return False
    if entity.get("is_alive") is False:
        return False
    return True


async def dm_node(state: GameState) -> dict:
    """
    分析玩家输入的意图。
    若 intent 为 command_done（系统指令已就地处理），直接跳过，不调用 LLM。
    兼容旧存档中偶发的 gift_given / item_used。
    DM 派发多人发言队列，并结算好感度变化，渲染 ControlledAgent 风格提示。
    """
    if state.get("intent") in ("command_done", "gift_given", "item_used"):
        return {}

    idle_intent = str(state.get("intent") or "").strip().lower()
    if idle_intent == "trigger_idle_banter":
        # 挂机闲聊：不调用 analyze_intent，随机一位队友作「主声线」模板；双人台词由 generation 一次 JSON 输出
        entities_raw = dict(state.get("entities") or {})
        if not entities_raw:
            entities_raw = copy.deepcopy(default_entities)
        entities = {k: dict(v) for k, v in entities_raw.items()}
        for k in entities:
            entities[k].setdefault("affection", 0)
            entities[k].setdefault("inventory", {})
            if not isinstance(entities[k].get("inventory"), dict):
                entities[k]["inventory"] = {}
        available_npcs = [
            entity_id
            for entity_id, entity in entities.items()
            if isinstance(entity, dict) and _is_idle_banter_speaker(str(entity_id), entity)
        ]
        if not available_npcs:
            print("🎲 DM Node: Idle banter (AFK) — no valid NPC speakers; skipping.")
            return {}
        current = random.choice(available_npcs)
        print("🎲 DM Node: Idle banter (AFK) — skipping intent LLM, speaker seed:", current)
        return {
            "entities": entities,
            "speaker_queue": [],
            "current_speaker": current,
            "speaker_responses": [],
            "intent": "trigger_idle_banter",
            "intent_context": {
                "difficulty_class": 12,
                "reason": "idle_banter",
                "action_actor": "player",
                "action_target": "",
            },
            "is_probing_secret": False,
        }

    print("🎲 DM Node: Analyzing intent...")
    entities_raw = dict(state.get("entities") or {})
    if not entities_raw:
        entities_raw = copy.deepcopy(default_entities)
    entities = {k: dict(v) for k, v in entities_raw.items()}
    for k in entities:
        entities[k].setdefault("affection", 0)
        entities[k].setdefault("inventory", {})
        if not isinstance(entities[k].get("inventory"), dict):
            entities[k]["inventory"] = {}
    available_npcs = list(entities.keys())
    if not available_npcs:
        available_npcs = ["unknown"]
    available_targets = list(
        dict.fromkeys(
            list(entities.keys()) + list((state.get("environment_objects") or {}).keys())
        )
    )
    fallback_speaker = first_entity_id(entities)
    current_npc_hp = entities.get(fallback_speaker, {}).get("hp", 20) if fallback_speaker != "unknown" else 20
    item_lore = _build_item_lore(state)
    structured_analysis = _build_structured_client_analysis(
        state=state,
        entities=entities,
        fallback_speaker=fallback_speaker,
    )
    if isinstance(structured_analysis, dict):
        structured_analysis = _apply_gatekeeper_boss_intro_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_hazard_lab_ui_text_fallback(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_study_chest_loot_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_gatekeeper_boss_resolution_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_gatekeeper_boss_strategy_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_gatekeeper_boss_intro_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_trap_disarm_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_secret_study_entry_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_secret_study_observation_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_diary_negotiation_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_trap_awareness_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_key_guidance_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_gatekeeper_mercy_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_scout_memory_echo_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_hazard_door_target_fallback(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_act3_hazard_lab_override(
            state=state,
            analysis=structured_analysis,
        )
        structured_analysis = _apply_act4_hazard_lab_override(
            state=state,
            analysis=structured_analysis,
        )
    else:
        boss_intro_fast_path = _apply_gatekeeper_boss_intro_override(
            state=state,
            analysis={},
        )
        if (
            isinstance(boss_intro_fast_path, dict)
            and str(boss_intro_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = boss_intro_fast_path
        ui_text_fast_path = _apply_hazard_lab_ui_text_fallback(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and
            isinstance(ui_text_fast_path, dict)
            and str(ui_text_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = ui_text_fast_path
        study_chest_loot_fast_path = _apply_study_chest_loot_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and
            isinstance(study_chest_loot_fast_path, dict)
            and str(study_chest_loot_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = study_chest_loot_fast_path
        boss_resolution_fast_path = _apply_gatekeeper_boss_resolution_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(boss_resolution_fast_path, dict)
            and str(boss_resolution_fast_path.get("action_type") or "").strip()
            and boss_resolution_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = boss_resolution_fast_path
        boss_strategy_fast_path = _apply_gatekeeper_boss_strategy_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(boss_strategy_fast_path, dict)
            and str(boss_strategy_fast_path.get("action_type") or "").strip()
            and boss_strategy_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = boss_strategy_fast_path
        trap_disarm_fast_path = _apply_trap_disarm_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and isinstance(trap_disarm_fast_path, dict)
            and str(trap_disarm_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = trap_disarm_fast_path
        secret_study_entry_fast_path = _apply_secret_study_entry_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(secret_study_entry_fast_path, dict)
            and str(secret_study_entry_fast_path.get("action_type") or "").strip()
            and secret_study_entry_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = secret_study_entry_fast_path
        secret_study_observation_fast_path = _apply_secret_study_observation_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(secret_study_observation_fast_path, dict)
            and str(secret_study_observation_fast_path.get("action_type") or "").strip()
            and secret_study_observation_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = secret_study_observation_fast_path
        diary_negotiation_fast_path = _apply_diary_negotiation_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and
            isinstance(diary_negotiation_fast_path, dict)
            and str(diary_negotiation_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = diary_negotiation_fast_path
        trap_awareness_fast_path = _apply_trap_awareness_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and isinstance(trap_awareness_fast_path, dict)
            and str(trap_awareness_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = trap_awareness_fast_path
        key_guidance_fast_path = _apply_key_guidance_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and isinstance(key_guidance_fast_path, dict)
            and str(key_guidance_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = key_guidance_fast_path
        gatekeeper_mercy_fast_path = _apply_gatekeeper_mercy_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(gatekeeper_mercy_fast_path, dict)
            and str(gatekeeper_mercy_fast_path.get("action_type") or "").strip()
            and gatekeeper_mercy_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = gatekeeper_mercy_fast_path
        memory_echo_fast_path = _apply_scout_memory_echo_override(
            state=state,
            analysis=structured_analysis or {},
        )
        if (
            isinstance(memory_echo_fast_path, dict)
            and str(memory_echo_fast_path.get("action_type") or "").strip()
            and memory_echo_fast_path is not (structured_analysis or {})
        ):
            structured_analysis = memory_echo_fast_path
        # Act3 side/rebuke choices should remain deterministic even without an explicit target/source payload.
        act3_fast_path = _apply_act3_hazard_lab_override(
            state=state,
            analysis={},
        )
        if (
            not structured_analysis
            and isinstance(act3_fast_path, dict)
            and str(act3_fast_path.get("action_type") or "").strip()
        ):
            structured_analysis = act3_fast_path
    if structured_analysis:
        analysis = structured_analysis
    else:
        try:
            analysis = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_intent,
                    state.get("user_input", ""),
                    flags=state.get("flags", {}),
                    time_of_day=state.get("time_of_day", "晨曦 (Morning)"),
                    hp=current_npc_hp,
                    available_npcs=available_npcs,
                    available_targets=available_targets,
                    item_lore=item_lore if item_lore else None,
                    active_dialogue_target=state.get("active_dialogue_target"),
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )
        except Exception:
            analysis = {
                "action_type": "IDLE",
                "difficulty_class": 0,
                "reason": "DM analyze timeout/failure fallback.",
                "is_probing_secret": False,
                "responders": [fallback_speaker] if fallback_speaker != "unknown" else ["analyst"],
                "affection_changes": {},
                "flags_changed": {},
                "item_transfers": [],
                "hp_changes": [],
                "action_actor": "player",
                "action_target": "",
            }

        analysis = _apply_act3_hazard_lab_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_gatekeeper_boss_intro_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_hazard_lab_ui_text_fallback(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_study_chest_loot_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_gatekeeper_boss_resolution_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_gatekeeper_boss_strategy_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_trap_disarm_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_secret_study_entry_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_secret_study_observation_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_diary_negotiation_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_trap_awareness_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_key_guidance_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_gatekeeper_mercy_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_scout_memory_echo_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_act4_hazard_lab_override(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )
        analysis = _apply_hazard_door_target_fallback(
            state=state,
            analysis=analysis if isinstance(analysis, dict) else {},
        )

    current_dialogue_target = str(state.get("active_dialogue_target") or "").strip().lower() or None
    next_dialogue_target = current_dialogue_target
    analyzed_action = str(analysis.get("action_type", "CHAT") or "CHAT").strip().upper()
    analyzed_target = str(analysis.get("action_target", "") or "").strip().lower()
    if analyzed_action == "START_DIALOGUE":
        next_dialogue_target = analyzed_target or current_dialogue_target
    elif analyzed_action == "DIALOGUE_REPLY":
        next_dialogue_target = current_dialogue_target or analyzed_target
        if not analysis.get("action_target") and next_dialogue_target:
            analysis["action_target"] = next_dialogue_target
    elif bool(analysis.get("clear_active_dialogue_target", False)):
        next_dialogue_target = None

    affection_changes = analysis.get("affection_changes", {})
    from ui.renderer import GameRenderer

    ui = GameRenderer()
    for npc_id, change in affection_changes.items():
        npc_id = str(npc_id).strip().lower()
        if npc_id in entities and isinstance(change, (int, float)) and change != 0:
            current_aff = entities[npc_id].get("affection", 0)
            new_aff = max(-100, min(100, current_aff + int(change)))
            entities[npc_id]["affection"] = new_aff
            npc_name_cn = entity_display_name(npc_id)
            delta = int(change)
            if delta > 0:
                ui.print_system_info(f"💡 [bold green][ {npc_name_cn} 赞同 (Approves) {delta:+d} ][/bold green]")
            else:
                ui.print_system_info(f"💔 [bold red][ {npc_name_cn} 不赞同 (Disapproves) {delta:+d} ][/bold red]")

    responders = analysis.get("responders")
    if isinstance(responders, list) and len(responders) > 0:
        queue = list(responders)
    else:
        queue = [fallback_speaker] if fallback_speaker != "unknown" else []
    current = queue.pop(0) if queue else fallback_speaker
    analysis_intent_context = (
        analysis.get("intent_context") if isinstance(analysis.get("intent_context"), dict) else {}
    )
    out = {
        "entities": entities,
        "speaker_queue": queue,
        "current_speaker": current,
        "speaker_responses": [],
        "intent": analysis.get("action_type", "CHAT"),
        "intent_context": {
            "difficulty_class": analysis.get("difficulty_class", 12),
            "reason": analysis.get("reason", ""),
            "action_actor": analysis.get("action_actor", "player"),
            "action_target": analysis.get("action_target", ""),
            "item_id": analysis.get("item_id", ""),
            "spell_id": analysis.get("spell_id", ""),
            "action_spell": analysis.get("spell_id", ""),
            "act3_choice": str(
                analysis_intent_context.get("act3_choice")
                or analysis.get("act3_choice")
                or ""
            ).strip(),
            "act4_post_combat_banter": bool(
                analysis_intent_context.get("act4_post_combat_banter")
                or analysis.get("act4_post_combat_banter")
                or False
            ),
            "player_sided_with_scout": bool(
                analysis_intent_context.get("player_sided_with_scout")
                if "player_sided_with_scout" in analysis_intent_context
                else bool(state.get("flags", {}).get("hazard_lab_player_sided_with_scout", False))
            ),
            "key_guidance_context": dict(analysis_intent_context.get("key_guidance_context") or {}),
            "diary_negotiation_context": dict(analysis_intent_context.get("diary_negotiation_context") or {}),
            "study_chest_loot_context": dict(analysis_intent_context.get("study_chest_loot_context") or {}),
            "trap_awareness_context": dict(analysis_intent_context.get("trap_awareness_context") or {}),
            "secret_study_entry_context": dict(analysis_intent_context.get("secret_study_entry_context") or {}),
            "secret_study_observation_context": dict(analysis_intent_context.get("secret_study_observation_context") or {}),
            "memory_echo_context": dict(analysis_intent_context.get("memory_echo_context") or {}),
            "gatekeeper_mercy_context": dict(analysis_intent_context.get("gatekeeper_mercy_context") or {}),
            "gatekeeper_boss_intro_context": dict(analysis_intent_context.get("gatekeeper_boss_intro_context") or {}),
            "gatekeeper_boss_strategy_context": dict(analysis_intent_context.get("gatekeeper_boss_strategy_context") or {}),
            "gatekeeper_boss_resolution_context": dict(analysis_intent_context.get("gatekeeper_boss_resolution_context") or {}),
            "action": str(analysis_intent_context.get("action") or "").strip(),
            "source": str(analysis_intent_context.get("source") or state.get("source") or "").strip().lower(),
        },
        "is_probing_secret": analysis.get("is_probing_secret", False),
        "active_dialogue_target": next_dialogue_target,
    }

    current_flags = dict(state.get("flags", {}))
    flags_changed = analysis.get("flags_changed", {})
    if isinstance(flags_changed, dict) and flags_changed:
        current_flags.update(flags_changed)
        out["flags"] = current_flags
        out["journal_events"] = out.get("journal_events", []) + [
            f"📜 [系统] 剧情世界线已变动: {list(flags_changed.keys())}"
        ]

    # -------------------------------------------------------------------------
    # [V2] 已剥夺 DM 的物理执行权：物品流转与 HP 变动全部由 generation_node 的工具调用完成
    # DM 只负责意图提取、DC 设定和好感度变动，不再调用 apply_physics
    # -------------------------------------------------------------------------
    # item_transfers = analysis.get("item_transfers", [])
    # hp_changes = analysis.get("hp_changes", [])
    # if not isinstance(item_transfers, list):
    #     item_transfers = []
    # if not isinstance(hp_changes, list):
    #     hp_changes = []
    # if item_transfers or hp_changes:
    #     player_inv = dict(state.get("player_inventory", {}))
    #     current_entities = dict(out["entities"])
    #     new_events = apply_physics(current_entities, player_inv, item_transfers, hp_changes)
    #     out["journal_events"] = out.get("journal_events", []) + new_events
    #     out["entities"] = current_entities
    #     if any(t.get("from") == "player" or t.get("to") == "player" for t in item_transfers if isinstance(t, dict)):
    #         out["player_inventory"] = player_inv

    return out


def advance_speaker_node(state: GameState) -> dict:
    """从 speaker_queue 弹出下一位，设为 current_speaker，实现多人连续发言。"""
    queue = list(state.get("speaker_queue", []))
    if not queue:
        return {}
    next_speaker = queue[0]
    remaining = queue[1:]
    return {"current_speaker": next_speaker, "speaker_queue": remaining}


def narration_node(state: GameState) -> dict:
    """
    DM 旁白节点 (V3): 负责渲染客观环境、动作结果，不再由 NPC 强行抢戏。
    使用 LLM 生成客观旁白，并严格锚定机制结果（掷骰 / DC / 成败）。
    """
    print("🎙️ [路由追踪] 进入 DM 旁白节点 (Narration Node)")

    latest_roll = state.get("latest_roll", {})
    roll_result = latest_roll.get("result", {}) if isinstance(latest_roll, dict) else {}
    intent = str((latest_roll or {}).get("intent", state.get("intent", "action"))).lower()
    dc = (latest_roll or {}).get("dc", "?")
    total = roll_result.get("total", "?") if isinstance(roll_result, dict) else "?"
    is_success = bool(roll_result.get("is_success", False)) if isinstance(roll_result, dict) else False
    rolls = roll_result.get("rolls", []) if isinstance(roll_result, dict) else []
    rolls_text = str(rolls if isinstance(rolls, list) and rolls else [roll_result.get("raw_roll", "?")])

    user_input = (state.get("user_input", "") or "").strip()
    time_of_day = state.get("time_of_day", "未知时段")
    journal_tail = list(state.get("journal_events", []))[-3:]
    journal_text = "\n".join(journal_tail) if journal_tail else "无"
    outcome = "成功" if is_success else "失败"

    system_prompt = (
        "你是桌游主持人 DM。请根据给定事实输出一段中文旁白，必须客观、简洁、具体。\n"
        "硬性规则：\n"
        "1) 只能依据已给事实，不得杜撰新道具/新人物/新结果。\n"
        "2) 必须与检定结果一致（成功就给有效发现或推进；失败就给受阻或无收获）。\n"
        "3) 输出 1-2 句，不要加前缀标签，不要输出思维过程。\n"
        f"时间：{time_of_day}\n"
        f"动作意图：{intent}\n"
        f"玩家输入：{user_input or '（无）'}\n"
        f"检定：Roll {rolls_text} -> Total {total} vs DC {dc}，结果：{outcome}\n"
        f"最近系统日志：\n{journal_text}\n"
    )

    fallback_text = (
        "你谨慎地检查了周围，线索逐渐浮出水面。"
        if is_success
        else "你反复确认了周围情况，但这次没有得到有价值的发现。"
    )
    try:
        raw_response = _run_blocking_with_timeout(
            generate_dialogue,
            system_prompt,
            conversation_history=[{"role": "user", "content": user_input or "继续"}],
        )
        parsed = parse_ai_response(raw_response)
        narration_text = (parsed.get("text") or "").strip() or fallback_text
    except Exception:
        narration_text = fallback_text

    attributed_msg = format_history_message("DM", narration_text)

    return {
        "messages": [AIMessage(content=attributed_msg, name="DM")],
        "final_response": narration_text,
    }
