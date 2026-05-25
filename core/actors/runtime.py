from __future__ import annotations

import time
from typing import Any, Dict, Tuple
from uuid import uuid4

from core.actors.contracts import ActorDecision, ReflectionRequest
from core.actors.views import ActorView
from core.campaigns import (
    ACT3_CHOICE_REBUKE_SCOUT,
    ACT3_CHOICE_SIDE_WITH_SCOUT,
)
from core.eval.telemetry import emit_telemetry
from core.events.models import DomainEvent
from core.systems.inventory import get_registry


def _new_event_id() -> str:
    return f"evt_{uuid4().hex}"


_GIFT_OFFER_MARKERS = ("给你", "送你", "拿着", "收下", "take this", "give you", "gift")
_ITEM_USE_MARKERS = ("喝", "服用", "使用", "drink", "use")
_MERCY_CHOICE_MARKERS = ("仁慈", "放过", "饶", "不杀", "mercy", "spare")
_CIVILIAN_PRIORITY_MARKERS = ("救平民", "先救", "救人", "save civilians", "save the civilian")
_ACT3_SIDE_MARKERS = (
    "侦察员说得对",
    "一起嘲笑",
    "同意侦察员",
    "side with scout",
    "mock gatekeeper",
)
_ACT3_REBUKE_MARKERS = (
    "侦察员，闭嘴",
    "侦察员闭嘴",
    "别拱火",
    "rebuke scout",
    "shut up scout",
)


def _contains_any(text: str, markers: Tuple[str, ...]) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in markers)


def _detect_party_choice_context(text: str) -> str:
    if _contains_any(text, _MERCY_CHOICE_MARKERS):
        return "mercy_choice"
    if _contains_any(text, _CIVILIAN_PRIORITY_MARKERS):
        return "civilian_priority_choice"
    return ""


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_hazard_lab_act3(actor_view: ActorView) -> bool:
    active_target = _normalize_id(actor_view.intent_context.get("action_target"))
    if active_target != "gatekeeper":
        return False
    visible_flags = actor_view.visible_flags if isinstance(actor_view.visible_flags, dict) else {}
    if bool(visible_flags.get("world_hazard_lab_intro_entered")):
        return True
    location_text = str(actor_view.current_location or "")
    return ("hazard_lab" in location_text.lower()) or ("实验室" in location_text)


def _detect_act3_choice_context(actor_view: ActorView) -> str:
    if not _is_hazard_lab_act3(actor_view):
        return ""

    explicit_choice = _normalize_id(
        actor_view.intent_context.get("act3_choice")
        or actor_view.intent_context.get("choice")
    )
    if explicit_choice in {ACT3_CHOICE_SIDE_WITH_SCOUT, ACT3_CHOICE_REBUKE_SCOUT}:
        return explicit_choice

    user_input = str(actor_view.user_input or "")
    normalized_input = user_input.strip().lower()
    if not normalized_input:
        return ""
    if any(marker in user_input or marker in normalized_input for marker in _ACT3_SIDE_MARKERS):
        return ACT3_CHOICE_SIDE_WITH_SCOUT
    if any(marker in user_input or marker in normalized_input for marker in _ACT3_REBUKE_MARKERS):
        return ACT3_CHOICE_REBUKE_SCOUT
    return ""


def _detect_act4_post_combat_context(actor_view: ActorView) -> bool:
    explicit = bool(actor_view.intent_context.get("act4_post_combat_banter"))
    if not explicit:
        return False
    visible_flags = actor_view.visible_flags if isinstance(actor_view.visible_flags, dict) else {}
    if not bool(visible_flags.get("world_hazard_lab_gatekeeper_defeated")):
        return False
    location_text = str(actor_view.current_location or "").strip().lower()
    return "hazard" in location_text or "实验室" in str(actor_view.current_location or "")


def _key_guidance_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("key_guidance_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "lab_key":
        return {}
    return dict(payload)


def _build_key_guidance_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context:
        return {}
    return {
        "type": "companion_guidance",
        "topic": "lab_key",
        "has_key": bool(context.get("has_lab_key", False)),
        "door_id": str(context.get("door_id") or "door_b_to_d"),
        "actor_id": actor_id,
    }


def _trap_awareness_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("trap_awareness_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "poison_trap":
        return {}
    return dict(payload)


def _build_trap_insight_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context or actor_id != "scout":
        return {}
    return {
        "topic": "poison_trap",
        "trap_id": str(context.get("trap_id") or "gas_trap_1"),
        "actor_id": actor_id,
        "can_disarm": bool(context.get("can_disarm", False)),
    }


def _secret_study_observation_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("secret_study_observation_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "secret_study_observation":
        return {}
    return dict(payload)


def _build_secret_study_observation_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context:
        return {}
    observations = context.get("observations") if isinstance(context.get("observations"), dict) else {}
    clue = str(observations.get(actor_id) or "").strip()
    if not clue:
        return {}
    return {
        "topic": "secret_study_observation",
        "actor_id": actor_id,
        "clue": clue,
        "location_id": str(context.get("location_id") or "room_c_secret_study"),
    }


def _memory_echo_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("memory_echo_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "memory_echo":
        return {}
    if str(payload.get("actor_id") or "").strip().lower() != "scout":
        return {}
    return dict(payload)


def _build_memory_echo_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if actor_id != "scout" or not context:
        return {}
    memory_type = str(context.get("memory_type") or "").strip()
    if memory_type not in {"rebuked_by_player", "sided_with_player"}:
        return {}
    return {
        "topic": "memory_echo",
        "memory_type": memory_type,
        "actor_id": actor_id,
    }


def _scout_memory_prefix(context: Dict[str, Any]) -> str:
    memory_type = str(context.get("memory_type") or "").strip()
    if memory_type == "rebuked_by_player":
        return "现在又需要我了？有趣。上次你让我闭嘴的时候可没这么客气。"
    if memory_type == "sided_with_player":
        return "这次我们又要一起做正确而残忍的选择了吗？我喜欢这种默契。"
    return ""


def _gatekeeper_mercy_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("gatekeeper_mercy_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "gatekeeper_mercy":
        return {}
    return dict(payload)


def _gatekeeper_boss_strategy_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("gatekeeper_boss_strategy_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "gatekeeper_boss_strategy":
        return {}
    return dict(payload)


def _build_gatekeeper_boss_strategy_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context:
        return {}
    stance = dict(context.get("stances") or {}).get(actor_id)
    if not stance:
        return {}
    return {
        "topic": "gatekeeper_boss_strategy",
        "stance": str(stance),
        "actor_id": actor_id,
        "target_id": str(context.get("target_id") or "gatekeeper"),
    }


def _gatekeeper_mercy_stance(*, actor_id: str, context: Dict[str, Any]) -> str:
    if not context:
        return ""
    memory_type = str(context.get("scout_memory_type") or "none").strip()
    if actor_id == "analyst":
        return "mercy"
    if actor_id == "tactician":
        return "execute"
    if actor_id == "scout":
        if memory_type == "rebuked_by_player":
            return "resentful"
        if memory_type == "sided_with_player":
            return "execute"
        return "mocking"
    return ""


def _build_gatekeeper_mercy_metadata(*, actor_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context:
        return {}
    if str(context.get("phase") or "stance").strip() != "stance":
        return {}
    stance = _gatekeeper_mercy_stance(actor_id=actor_id, context=context)
    if not stance:
        return {}
    payload = {
        "topic": "gatekeeper_mercy",
        "stance": stance,
        "actor_id": actor_id,
        "target_id": str(context.get("target_id") or "gatekeeper"),
    }
    if actor_id == "scout":
        payload["memory_echo"] = str(context.get("scout_memory_type") or "none")
    return payload


def _mercy_memory_note(actor_id: str, choice: str, *, diary_decoded: bool, memory_type: str) -> str:
    if choice == "mercy":
        if actor_id == "analyst":
            return "玩家放过了被危害实验扭曲的 Gatekeeper。怜悯有代价，但这次我认同。"
        if actor_id == "tactician":
            return "玩家放过了危险的失控实验体。软弱会留下后患。"
        if actor_id == "scout":
            if memory_type == "rebuked_by_player":
                return "玩家训斥过我，如今又选择对 Gatekeeper 表演仁慈。我会记住这种方便的道德。"
            return "玩家放过了 Gatekeeper。仁慈未必聪明，但至少没有妨碍我继续前进。"
    if choice == "execute":
        if actor_id == "analyst":
            suffix = "，尽管日记已经说明他也是受害者" if diary_decoded else "，尽管真相仍不完整"
            return f"玩家处决了 Gatekeeper{suffix}。这让我不安。"
        if actor_id == "tactician":
            return "玩家处决了 Gatekeeper，选择了果断行动。危险不该被留下。"
        if actor_id == "scout":
            return "玩家选择了残忍而实际的办法。至少这一次没有浪费时间。"
    return ""


def _build_gatekeeper_mercy_resolution_events(
    *,
    actor_id: str,
    turn_index: int,
    context: Dict[str, Any],
) -> Tuple[DomainEvent, ...]:
    if actor_id != "analyst" or not context:
        return ()
    if str(context.get("phase") or "").strip() != "resolution":
        return ()
    choice = str(context.get("choice") or "").strip()
    if choice not in {"mercy", "execute"}:
        return ()

    diary_decoded = bool(context.get("diary_decoded", False))
    memory_type = str(context.get("scout_memory_type") or "none").strip()
    if choice == "mercy":
        affection_deltas = {"analyst": 2, "tactician": -1}
        if memory_type == "rebuked_by_player":
            affection_deltas["scout"] = -1
        status_set = "spared"
        faction_set = "neutralized"
        result_flag = "hazard_lab_gatekeeper_spared"
        reason = "gatekeeper_mercy_spared"
    else:
        affection_deltas = {"analyst": -2 if diary_decoded else -1, "tactician": 2}
        if memory_type == "sided_with_player":
            affection_deltas["scout"] = 1
        status_set = "dead"
        faction_set = "defeated"
        result_flag = "hazard_lab_gatekeeper_executed"
        reason = "gatekeeper_mercy_executed"

    events = [
        DomainEvent(
            event_id=_new_event_id(),
            event_type="actor_negotiation_outcome_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={
                "target_actor_id": "gatekeeper",
                "status_set": status_set,
                "faction_set": faction_set,
                "force_hostile": False,
                "trigger_combat": False,
                "reason": reason,
            },
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": result_flag, "value": True},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_mercy_resolved", "value": True},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_mercy_window", "value": False},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_key_available", "value": True},
        ),
    ]
    if choice == "execute":
        events.append(
            DomainEvent(
                event_id=_new_event_id(),
                event_type="world_flag_changed",
                actor_id=actor_id,
                turn_index=turn_index,
                visibility="party",
                payload={"key": "world_hazard_lab_gatekeeper_defeated", "value": True},
            )
        )

    for target_actor_id, delta in affection_deltas.items():
        if delta == 0:
            continue
        events.append(
            DomainEvent(
                event_id=_new_event_id(),
                event_type="actor_affection_changed",
                actor_id=actor_id,
                turn_index=turn_index,
                visibility="party",
                payload={
                    "target_actor_id": target_actor_id,
                    "delta": delta,
                    "reason": f"gatekeeper_mercy_{choice}",
                },
            )
        )

    for target_actor_id in ("analyst", "tactician", "scout"):
        memory_text = _mercy_memory_note(
            target_actor_id,
            choice,
            diary_decoded=diary_decoded,
            memory_type=memory_type,
        )
        if not memory_text:
            continue
        events.append(
            DomainEvent(
                event_id=_new_event_id(),
                event_type="actor_memory_update_requested",
                actor_id=target_actor_id,
                turn_index=turn_index,
                visibility="private",
                payload={
                    "scope": "actor_private",
                    "memory_type": "gatekeeper_mercy_choice",
                    "text": memory_text,
                },
            )
        )
    return tuple(events)


def _diary_negotiation_context(actor_view: ActorView) -> Dict[str, Any]:
    payload = actor_view.intent_context.get("diary_negotiation_context")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("topic") or "").strip().lower() != "gatekeeper_elixir_truth":
        return {}
    if not bool(payload.get("decoded_diary", False)):
        return {}
    return dict(payload)


def _build_diary_pressure_events(
    *,
    actor_id: str,
    turn_index: int,
    context: Dict[str, Any],
) -> Tuple[DomainEvent, ...]:
    if actor_id != "analyst" or not context:
        return ()
    try:
        current_patience = int(context.get("patience_current", 10) or 10)
    except (TypeError, ValueError):
        current_patience = 10
    next_patience = max(0, current_patience - 1)
    return (
        DomainEvent(
            event_id=_new_event_id(),
            event_type="actor_negotiation_outcome_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={
                "target_actor_id": "gatekeeper",
                "patience_set": next_patience,
                "fear_delta": 1,
                "paranoia_delta": 1,
                "force_hostile": False,
                "trigger_combat": False,
                "reason": "diary_evidence_pressure",
            },
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_truth_pressure", "value": True},
        ),
    )


def _build_act3_memory_note(choice_context: str) -> str:
    if choice_context == ACT3_CHOICE_SIDE_WITH_SCOUT:
        return "玩家与我一起嘲笑了 Gatekeeper，这种默契让我满意。"
    if choice_context == ACT3_CHOICE_REBUKE_SCOUT:
        return "玩家当众训斥了我，我会记住这笔账。"
    return ""


def _build_act3_runtime_events(
    *,
    actor_id: str,
    turn_index: int,
    choice_context: str,
) -> Tuple[DomainEvent, ...]:
    if choice_context not in {ACT3_CHOICE_SIDE_WITH_SCOUT, ACT3_CHOICE_REBUKE_SCOUT}:
        return ()

    sided_with_scout = choice_context == ACT3_CHOICE_SIDE_WITH_SCOUT
    affection_delta = 2 if sided_with_scout else -3
    fear_delta = 2 if sided_with_scout else 3
    paranoia_delta = 1 if sided_with_scout else 4
    outcome_reason = "mockery_escalation" if sided_with_scout else "paranoia_meltdown"

    events = [
        DomainEvent(
            event_id=_new_event_id(),
            event_type="actor_affection_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={
                "target_actor_id": actor_id,
                "delta": affection_delta,
                "reason": f"act3_{choice_context}",
            },
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="actor_memory_update_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="private",
            payload={
                "scope": "actor_private",
                "memory_type": "relationship",
                "text": _build_act3_memory_note(choice_context),
            },
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="actor_negotiation_outcome_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={
                "target_actor_id": "gatekeeper",
                "patience_set": 0,
                "fear_delta": fear_delta,
                "paranoia_delta": paranoia_delta,
                "force_hostile": True,
                "trigger_combat": True,
                "reason": outcome_reason,
            },
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_negotiation_started", "value": True},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_scout_mocked_gatekeeper", "value": True},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_player_sided_with_scout", "value": sided_with_scout},
        ),
        DomainEvent(
            event_id=_new_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": "hazard_lab_gatekeeper_combat_triggered", "value": True},
        ),
    ]
    return tuple(events)


def _build_choice_memory_note(*, actor_id: str, choice_context: str) -> str:
    if choice_context == "mercy_choice":
        if actor_id == "tactician":
            return "tactician 记录小队抉择：反对仁慈放敌。"
        if actor_id == "analyst":
            return "analyst 记录小队抉择：谨慎支持仁慈。"
        if actor_id == "scout":
            return "scout 记录小队抉择：对仁慈选择保持讥讽。"
        return f"{actor_id} 记录小队抉择：对仁慈选择有明确态度。"
    if choice_context == "civilian_priority_choice":
        if actor_id == "analyst":
            return "analyst 记录小队抉择：优先救援平民。"
        if actor_id == "scout":
            return "scout 记录小队抉择：对英雄式优先级保持怀疑。"
        if actor_id == "tactician":
            return "tactician 记录小队抉择：反对延后追击敌人。"
        return f"{actor_id} 记录小队抉择：对救援优先级表达立场。"
    return ""


def _build_act4_memory_note(*, actor_id: str, sided_with_scout: bool) -> str:
    if actor_id == "analyst":
        return "Gatekeeper 倒下后，我仍能感到危害残渣。拿到钥匙不代表安全。"
    if actor_id == "tactician":
        return "目标已完成：拿钥匙、开门、撤离。拖延毫无意义。"
    if sided_with_scout:
        return "玩家在 Gatekeeper 事件里与我同调。拿到钥匙后应立刻离开。"
    return "玩家曾当众压我一头，但钥匙到手后我依旧选择推进撤离。"


def _resolve_item_id_from_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""

    direct_map = {
        "治疗药水": "healing_potion",
        "healing_potion": "healing_potion",
        "potion": "healing_potion",
        "未知协议令牌": "restricted_signal_token",
        "restricted signal token": "restricted_signal_token",
        "神器": "mysterious_artifact",
        "artifact": "mysterious_artifact",
    }
    for key, item_id in direct_map.items():
        if key in normalized:
            return item_id

    registry = get_registry()
    for token in normalized.replace("，", " ").replace(",", " ").split():
        resolved = registry.resolve_item_id(token)
        if resolved:
            return str(resolved).strip().lower()
    return ""


def _gift_reject_reason(actor_id: str, item_id: str) -> str:
    # V1.3 baseline policy: Scout rejects unsolicited gifts to validate reject/return protocol.
    if actor_id == "scout" and item_id in {"healing_potion", "restricted_signal_token", "mysterious_artifact"}:
        return "unwanted_gift"
    return ""


def _build_social_action(actor_view: ActorView) -> Dict[str, Any]:
    user_input = str(actor_view.user_input or "").strip()
    if not user_input:
        return {}
    item_id = _resolve_item_id_from_text(user_input)
    if not item_id:
        return {}

    actor_id = str(actor_view.actor_id or "").strip().lower()
    if _contains_any(user_input, _GIFT_OFFER_MARKERS):
        reject_reason = _gift_reject_reason(actor_id, item_id)
        if reject_reason:
            return {
                "action_type": "gift_reject",
                "actor_id": actor_id,
                "target_actor_id": "player",
                "item_id": item_id,
                "quantity": 1,
                "accepted": False,
                "reason": reject_reason,
            }
        return {
            "action_type": "gift_accept",
            "actor_id": actor_id,
            "target_actor_id": "player",
            "item_id": item_id,
            "quantity": 1,
            "accepted": True,
            "reason": "accepted_gift",
        }

    if _contains_any(user_input, _ITEM_USE_MARKERS):
        return {
            "action_type": "item_use",
            "actor_id": actor_id,
            "target_actor_id": actor_id,
            "item_id": item_id,
            "quantity": 1,
            "accepted": True,
            "reason": "item_use_requested",
        }
    return {}


def _social_action_to_transaction_payload(action: Dict[str, Any]) -> Dict[str, Any]:
    action_type = str(action.get("action_type") or "").strip().lower()
    accepted = bool(action.get("accepted", False))
    actor_id = str(action.get("actor_id") or "").strip().lower()
    item_id = str(action.get("item_id") or "").strip().lower()
    quantity = max(1, int(action.get("quantity", 1) or 1))
    reason = str(action.get("reason") or "").strip()

    if action_type == "gift_accept":
        return {
            "transaction_type": "transfer",
            "from_entity": "player",
            "to_entity": actor_id,
            "item": item_id,
            "quantity": quantity,
            "accepted": accepted,
            "reason": reason,
        }
    if action_type == "gift_reject":
        return {
            "transaction_type": "no_op",
            "from_entity": "player",
            "to_entity": actor_id,
            "item": item_id,
            "quantity": quantity,
            "accepted": accepted,
            "reason": reason,
        }
    if action_type == "item_use":
        return {
            "transaction_type": "consume",
            "from_entity": actor_id,
            "to_entity": "consumed",
            "item": item_id,
            "quantity": quantity,
            "accepted": accepted,
            "reason": reason,
        }
    return {
        "transaction_type": "no_op",
        "from_entity": actor_id,
        "to_entity": actor_id,
        "item": item_id,
        "quantity": quantity,
        "accepted": False,
        "reason": "unsupported_social_action",
    }


class TemplateActorRuntime:
    """
    Phase 3 V1 runtime:
    - actor-scoped input
    - deterministic decision output
    - emits events only (no direct world mutation)
    """

    def __init__(self, actor_id: str) -> None:
        self.actor_id = str(actor_id or "").strip().lower()

    async def decide(self, actor_view: ActorView) -> ActorDecision:
        started_at = time.perf_counter()
        user_input = str(actor_view.user_input or "").strip()
        if not user_input:
            decision = ActorDecision(actor_id=self.actor_id, kind="silent")
            duration_ms = max(0, int(round((time.perf_counter() - started_at) * 1000)))
            emit_telemetry(
                "llm_call",
                component="actor_runtime",
                actor_id=self.actor_id,
                provider="template_runtime",
                model="template_actor_runtime",
                success=True,
                duration_ms=duration_ms,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            emit_telemetry(
                "actor_runtime_decision",
                actor_id=self.actor_id,
                decision_kind=decision.kind,
                emitted_event_count=0,
                reflection_request_count=0,
                duration_ms=duration_ms,
            )
            return decision

        social_action = _build_social_action(actor_view)
        choice_context = _detect_party_choice_context(user_input)
        act3_choice_context = _detect_act3_choice_context(actor_view)
        act4_post_combat_context = _detect_act4_post_combat_context(actor_view)
        key_guidance_context = _key_guidance_context(actor_view)
        trap_awareness_context = _trap_awareness_context(actor_view)
        secret_study_observation_context = _secret_study_observation_context(actor_view)
        diary_negotiation_context = _diary_negotiation_context(actor_view)
        memory_echo_context = _memory_echo_context(actor_view)
        gatekeeper_mercy_context = _gatekeeper_mercy_context(actor_view)
        gatekeeper_boss_strategy_context = _gatekeeper_boss_strategy_context(actor_view)
        if diary_negotiation_context:
            social_action = {}
        if gatekeeper_mercy_context or gatekeeper_boss_strategy_context:
            social_action = {}
            choice_context = ""
        spoken_text = self._compose_reply(
            actor_view,
            social_action=social_action,
            choice_context=choice_context,
            act3_choice_context=act3_choice_context,
            act4_post_combat_context=act4_post_combat_context,
            key_guidance_context=key_guidance_context,
            trap_awareness_context=trap_awareness_context,
            secret_study_observation_context=secret_study_observation_context,
            diary_negotiation_context=diary_negotiation_context,
            memory_echo_context=memory_echo_context,
            gatekeeper_mercy_context=gatekeeper_mercy_context,
            gatekeeper_boss_strategy_context=gatekeeper_boss_strategy_context,
        )
        spoke_payload: Dict[str, Any] = {"text": spoken_text}
        guidance_metadata = _build_key_guidance_metadata(
            actor_id=self.actor_id,
            context=key_guidance_context,
        )
        if guidance_metadata:
            spoke_payload["guidance"] = guidance_metadata
        trap_metadata = _build_trap_insight_metadata(
            actor_id=self.actor_id,
            context=trap_awareness_context,
        )
        if trap_metadata:
            spoke_payload["trap_insight"] = trap_metadata
        study_metadata = _build_secret_study_observation_metadata(
            actor_id=self.actor_id,
            context=secret_study_observation_context,
        )
        if study_metadata:
            spoke_payload["secret_study_observation"] = study_metadata
        memory_metadata = _build_memory_echo_metadata(
            actor_id=self.actor_id,
            context=memory_echo_context,
        )
        if memory_metadata:
            spoke_payload["memory_echo"] = memory_metadata
        mercy_metadata = _build_gatekeeper_mercy_metadata(
            actor_id=self.actor_id,
            context=gatekeeper_mercy_context,
        )
        if mercy_metadata:
            spoke_payload["gatekeeper_mercy"] = mercy_metadata
        strategy_metadata = _build_gatekeeper_boss_strategy_metadata(
            actor_id=self.actor_id,
            context=gatekeeper_boss_strategy_context,
        )
        if strategy_metadata:
            spoke_payload["gatekeeper_boss_strategy"] = strategy_metadata
        events = [
            DomainEvent(
                event_id=_new_event_id(),
                event_type="actor_spoke",
                actor_id=self.actor_id,
                turn_index=int(actor_view.turn_count or 0),
                visibility="party",
                payload=spoke_payload,
            )
        ]
        if social_action:
            transaction_payload = _social_action_to_transaction_payload(social_action)
            event_payload: Dict[str, Any] = {
                "social_action": social_action,
                "transaction": transaction_payload,
            }
            if (
                str(transaction_payload.get("transaction_type") or "") == "consume"
                and str(transaction_payload.get("item") or "") == "healing_potion"
            ):
                event_payload["hp_changes"] = [{"target": self.actor_id, "amount": 5}]
            events.append(
                DomainEvent(
                    event_id=_new_event_id(),
                    event_type="actor_item_transaction_requested",
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    visibility="party",
                    payload=event_payload,
                )
            )
            memory_text = (
                f"{self.actor_id} 记录社交物品互动："
                f"{social_action.get('action_type')} {social_action.get('item_id')} ({social_action.get('reason')})"
            )
            events.append(
                DomainEvent(
                    event_id=_new_event_id(),
                    event_type="actor_memory_update_requested",
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    visibility="private",
                    payload={"text": memory_text, "memory_type": "social_item_interaction"},
                )
            )
        elif choice_context:
            choice_memory_note = _build_choice_memory_note(
                actor_id=self.actor_id,
                choice_context=choice_context,
            )
            if choice_memory_note:
                events.append(
                    DomainEvent(
                        event_id=_new_event_id(),
                        event_type="actor_memory_update_requested",
                        actor_id=self.actor_id,
                        turn_index=int(actor_view.turn_count or 0),
                        visibility="private",
                    payload={"text": choice_memory_note, "memory_type": "party_choice_reaction"},
                    )
                )
        if self.actor_id == "scout" and act3_choice_context:
            events.extend(
                _build_act3_runtime_events(
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    choice_context=act3_choice_context,
                )
            )
        if act4_post_combat_context and self.actor_id in {"scout", "analyst", "tactician"}:
            sided_with_scout = bool(actor_view.intent_context.get("player_sided_with_scout", False))
            memory_note = _build_act4_memory_note(
                actor_id=self.actor_id,
                sided_with_scout=sided_with_scout,
            )
            events.append(
                DomainEvent(
                    event_id=_new_event_id(),
                    event_type="actor_memory_update_requested",
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    visibility="private",
                    payload={
                        "scope": "actor_private",
                        "memory_type": "post_combat_reflection",
                        "text": memory_note,
                    },
                )
            )
        if diary_negotiation_context:
            events.extend(
                _build_diary_pressure_events(
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    context=diary_negotiation_context,
                )
            )
        if gatekeeper_mercy_context:
            events.extend(
                _build_gatekeeper_mercy_resolution_events(
                    actor_id=self.actor_id,
                    turn_index=int(actor_view.turn_count or 0),
                    context=gatekeeper_mercy_context,
                )
            )
        requested_reflections: Tuple[ReflectionRequest, ...] = ()
        if any(token in user_input for token in ("秘密", "真相", "协议立场", "过去")):
            requested_reflections = (
                ReflectionRequest(
                    actor_id=self.actor_id,
                    reason="sensitive_topic_triggered",
                    priority=2,
                    source_turn=int(actor_view.turn_count or 0),
                    payload={"user_input": user_input},
                ),
            )
        decision = ActorDecision(
            actor_id=self.actor_id,
            kind="speak",
            spoken_text=spoken_text,
            thought_summary="runtime_decision",
            emitted_events=tuple(events),
            requested_reflections=requested_reflections,
        )
        duration_ms = max(0, int(round((time.perf_counter() - started_at) * 1000)))
        emit_telemetry(
            "llm_call",
            component="actor_runtime",
            actor_id=self.actor_id,
            provider="template_runtime",
            model="template_actor_runtime",
            success=True,
            duration_ms=duration_ms,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        emit_telemetry(
            "actor_runtime_decision",
            actor_id=self.actor_id,
            decision_kind=decision.kind,
            emitted_event_count=len(decision.emitted_events),
            reflection_request_count=len(decision.requested_reflections),
            duration_ms=duration_ms,
        )
        return decision

    async def reflect(self, request: ReflectionRequest) -> Tuple[DomainEvent, ...]:
        belief_text = f"{self.actor_id} 在反思中记录：{request.reason}"
        return (
            DomainEvent(
                event_id=_new_event_id(),
                event_type="actor_belief_updated",
                actor_id=self.actor_id,
                turn_index=int(request.source_turn or 0),
                visibility="director_only",
                payload={
                    "reason": request.reason,
                    "belief": belief_text,
                    "priority": int(request.priority or 0),
                },
            ),
            DomainEvent(
                event_id=_new_event_id(),
                event_type="actor_memory_update_requested",
                actor_id=self.actor_id,
                turn_index=int(request.source_turn or 0),
                visibility="private",
                payload={"text": belief_text},
            ),
        )

    def _compose_reply(
        self,
        actor_view: ActorView,
        *,
        social_action: Dict[str, Any],
        choice_context: str = "",
        act3_choice_context: str = "",
        act4_post_combat_context: bool = False,
        key_guidance_context: Dict[str, Any] | None = None,
        trap_awareness_context: Dict[str, Any] | None = None,
        secret_study_observation_context: Dict[str, Any] | None = None,
        diary_negotiation_context: Dict[str, Any] | None = None,
        memory_echo_context: Dict[str, Any] | None = None,
        gatekeeper_mercy_context: Dict[str, Any] | None = None,
        gatekeeper_boss_strategy_context: Dict[str, Any] | None = None,
    ) -> str:
        memory_prefix = ""
        if self.actor_id == "scout" and memory_echo_context:
            memory_prefix = _scout_memory_prefix(memory_echo_context)

        action_type = str(social_action.get("action_type") or "").strip().lower()
        if action_type == "gift_accept":
            if self.actor_id == "analyst":
                return "……我收下了。别声张。"
            return "我收下了。继续。"
        if action_type == "gift_reject":
            if self.actor_id == "scout":
                return "把你的施舍收回去。我不需要。"
            return "不需要，把它拿回去。"
        if action_type == "item_use":
            return "我用了它。继续。"

        if choice_context == "mercy_choice":
            if self.actor_id == "tactician":
                return "软弱。放过敌人只会让战斗回到我们身上。"
            if self.actor_id == "analyst":
                return "仁慈不是罪，但你要承担后果。"
            if self.actor_id == "scout":
                return "真意外，你今天居然选了仁慈。"
            return "你选择了仁慈，希望代价值得。"
        if choice_context == "civilian_priority_choice":
            if self.actor_id == "analyst":
                return "先救平民是对的，至少今晚我能睡得着。"
            if self.actor_id == "scout":
                return "你要当英雄？那就别拖慢我们。"
            if self.actor_id == "tactician":
                return "先救弱者会耗掉追击窗口。"
            return "先救平民可以，但别失去节奏。"

        if gatekeeper_boss_strategy_context:
            if self.actor_id == "scout":
                return "他害怕得快把钥匙捏碎了。给我一个机会，我能从他手里把钥匙弄出来。"
            if self.actor_id == "analyst":
                return "他被这里的东西毁了。逼太狠，毒气罐可能会先炸。"
            if self.actor_id == "tactician":
                return "杀掉守门人，拿走钥匙，打开门。"
            return "钥匙、毒气和 Gatekeeper 的恐惧绑在一起。先选路线。"

        if gatekeeper_mercy_context:
            phase = str(gatekeeper_mercy_context.get("phase") or "stance").strip()
            choice = str(gatekeeper_mercy_context.get("choice") or "").strip()
            diary_decoded = bool(gatekeeper_mercy_context.get("diary_decoded", False))
            scout_memory_type = str(gatekeeper_mercy_context.get("scout_memory_type") or "none").strip()
            if phase == "resolution":
                if choice == "mercy":
                    if self.actor_id == "analyst":
                        return "你选择放过 Gatekeeper。日记里的真相不会让他无辜，但说明他也是危害实验的受害者。"
                    return "Gatekeeper 被放过了。"
                if self.actor_id == "analyst":
                    return "你选择处决 Gatekeeper。即使他危险，日记里的危害实验真相也让这件事更沉重。"
                return "Gatekeeper 被处决了。"

            if self.actor_id == "analyst":
                if diary_decoded:
                    return "日记说明 Gatekeeper 是危害实验扭曲出的受害者。放过他更危险，却也更像我们还能保住的底线。"
                return "他已经崩溃了。先别急着杀，至少弄清楚他还能说出什么。"
            if self.actor_id == "tactician":
                return "处决他。失控实验体就是风险，留下活口只会让危险再站起来。"
            if self.actor_id == "scout":
                if scout_memory_type == "rebuked_by_player":
                    return "现在又要装仁慈了？有趣。你训斥我时可没这么温柔。随你，但别假装这很高尚。"
                if scout_memory_type == "sided_with_player":
                    return "我们已经一起嘲笑过他了，亲爱的。别浪费时间，处决或丢下他都比同情更实用。"
                return "可怜的 Gatekeeper。真动人。处决他，或者放过他继续碍事，选一个。"
            return "Gatekeeper 已经倒下。现在要决定放过还是处决他。"

        if key_guidance_context:
            has_lab_key = bool(key_guidance_context.get("has_lab_key", False))
            if has_lab_key:
                if self.actor_id == "scout":
                    base = "lab_key 都到手了，亲爱的，去打开 door_b_to_d 那扇实验室门。"
                    return f"{memory_prefix}{base}" if memory_prefix else base
                if self.actor_id == "analyst":
                    return "钥匙在你手上，打开实验室门，别等这里的力量再聚拢。"
                if self.actor_id == "tactician":
                    return "钥匙已取得。打开 door_b_to_d 实验室门，面对里面的东西。"
                return "钥匙已经在手上，打开实验室门。"

            if self.actor_id == "scout":
                base = "lab_key 不在你包里，亲爱的。先找书房和暗门，翻 study_chest；或者让我撬锁。"
                return f"{memory_prefix}{base}" if memory_prefix else base
            if self.actor_id == "analyst":
                return "没有钥匙就别硬说能开。危害痕迹指向书房和 hazard_diary，先读日记再搜箱子。"
            if self.actor_id == "tactician":
                return "没有 lab_key。钥匙、撬锁、破门，选一个。别浪费时间。"
            return "没有钥匙。先找书房线索，或尝试撬锁。"

        if trap_awareness_context:
            if self.actor_id == "scout":
                base = "停。地面有毒气压力板，旁边还有可疑喷口。别再往前踩；我可以解除 gas_trap_1。"
                return f"{memory_prefix}{base}" if memory_prefix else base
            return "Scout 看到了陷阱，先别继续往前。"

        if secret_study_observation_context:
            if self.actor_id == "scout":
                return "钥匙、路线、账本、逃生口全写在这些边角里。这里不是书房，是某人给自己留的退路。"
            if self.actor_id == "analyst":
                return "毒、死亡、意志控制混在一起。这里的药剂不是治疗，是把活物拖向危害术的缰绳。"
            if self.actor_id == "tactician":
                return "足够了。线索已经指向钥匙和训练无人机，继续翻纸只会浪费战斗前的时间。"
            return "书房里留下了足够多的线索。"

        if diary_negotiation_context:
            if self.actor_id == "analyst":
                return "日记说得够清楚了：这是危害污染，不是天赋。继续逼问很危险，但能动摇他。"
            if self.actor_id == "scout":
                return "原来那瓶聪明药把你变得这么可悲。钥匙呢，Gatekeeper？"
            if self.actor_id == "tactician":
                return "这是弱点。逼问他，把钥匙逼出来。"
            return "日记里的证据可以压住他的谎话。"

        if memory_prefix:
            if str((memory_echo_context or {}).get("memory_type") or "") == "rebuked_by_player":
                return f"{memory_prefix}说吧，我会帮，但别装得像什么都没发生。"
            return f"{memory_prefix}说吧，我们一起把局面弄得更有趣。"

        if self.actor_id == "scout":
            if act3_choice_context == ACT3_CHOICE_SIDE_WITH_SCOUT:
                return "看见了吗，Gatekeeper？连玩家都觉得你可笑。"
            if act3_choice_context == ACT3_CHOICE_REBUKE_SCOUT:
                return "当众让我闭嘴？行，我记下了。"

        if act4_post_combat_context:
            if self.actor_id == "analyst":
                return "危害气息淡了些，但别松懈。我们先离开这里。"
            if self.actor_id == "tactician":
                return "废话够了。开门，继续前进。"
            if self.actor_id == "scout":
                sided_with_scout = bool(actor_view.intent_context.get("player_sided_with_scout", False))
                if sided_with_scout:
                    return "至少你这次没拖后腿。钥匙到手，走人。"
                return "别误会，我只是想离开这鬼地方。"

        user_input = str(actor_view.user_input or "").strip()
        if "谢谢" in user_input:
            return "收起你的客气，把眼睛放在前方。"
        if "开门" in user_input:
            return "门会开，但别指望我替你收拾烂摊子。"
        if "攻击" in user_input or "战斗" in user_input:
            return "我在看着。你最好别失手。"
        return "我听见了。继续。"
