from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from langchain_core.messages import AIMessage

from core.actors.contracts import StatePatch
from core.engine.physics import apply_physics
from core.events.models import (
    DomainEvent,
    item_transaction_from_payload,
    item_transaction_to_transfer_payload,
    social_action_from_payload,
)
from core.utils.text_processor import format_history_message


def _apply_actor_spoke(
    *,
    event: DomainEvent,
    entities: Dict[str, Dict[str, Any]],
    environment_objects: Dict[str, Dict[str, Any]],
    flags: Dict[str, Any],
    messages: List[Any],
    speaker_responses: List[Tuple[str, str]],
    journal_events: List[str],
) -> str:
    text = str(event.payload.get("text") or "").strip()
    if not text:
        return ""
    speaker_id = str(event.actor_id or "").strip().lower()
    messages.append(AIMessage(content=format_history_message(speaker_id, text), name=speaker_id))
    speaker_responses.append((speaker_id, text))
    journal_events.append(f"💬 [台词] {speaker_id}: \"{text}\"")
    guidance = event.payload.get("guidance") if isinstance(event.payload, dict) else None
    if isinstance(guidance, dict) and str(guidance.get("type") or "") == "companion_guidance":
        topic = str(guidance.get("topic") or "").strip()
        door_id = str(guidance.get("door_id") or "").strip()
        has_key = bool(guidance.get("has_key", False))
        journal_events.append(
            f"[队友建议] {speaker_id} notices inventory/world state: topic={topic} has_key={has_key} door_id={door_id}."
        )
    trap_insight = event.payload.get("trap_insight") if isinstance(event.payload, dict) else None
    if isinstance(trap_insight, dict) and str(trap_insight.get("topic") or "") == "poison_trap":
        trap_id = str(trap_insight.get("trap_id") or "gas_trap_1").strip() or "gas_trap_1"
        journal_events.append(f"[陷阱感知] {speaker_id} -> {trap_id}")
        flags["hazard_lab_poison_trap_revealed"] = True
        flags["act2_corridor_entered"] = True
        flags["act2_scout_perception_checked"] = True
        flags["act2_scout_perception_success"] = True
        flags["act2_gas_trap_revealed"] = True
        flags["scout_detected_gas_trap"] = {
            "value": True,
            "visibility": {
                "scope": "actor",
                "actors": ["scout"],
                "reason": "trap_awareness",
            },
        }
        for bucket in (entities, environment_objects):
            trap = bucket.get(trap_id)
            if not isinstance(trap, dict):
                continue
            trap["is_hidden"] = False
            trap["status"] = "revealed"
    study_observation = event.payload.get("secret_study_observation") if isinstance(event.payload, dict) else None
    if isinstance(study_observation, dict) and str(study_observation.get("topic") or "") == "secret_study_observation":
        clue = str(study_observation.get("clue") or "").strip()
        if clue:
            journal_events.append(f"[书房观察] {speaker_id} -> {clue}")
    memory_echo = event.payload.get("memory_echo") if isinstance(event.payload, dict) else None
    if isinstance(memory_echo, dict) and str(memory_echo.get("topic") or "") == "memory_echo":
        memory_type = str(memory_echo.get("memory_type") or "").strip()
        if speaker_id == "scout" and memory_type in {"rebuked_by_player", "sided_with_player"}:
            journal_events.append(f"[记忆回响] scout -> {memory_type}")
            flags["hazard_lab_scout_memory_echo_seen"] = True
            if memory_type == "rebuked_by_player":
                flags["hazard_lab_scout_rebuke_echo_seen"] = True
            else:
                flags["hazard_lab_scout_complicity_echo_seen"] = True
    gatekeeper_mercy = event.payload.get("gatekeeper_mercy") if isinstance(event.payload, dict) else None
    if isinstance(gatekeeper_mercy, dict) and str(gatekeeper_mercy.get("topic") or "") == "gatekeeper_mercy":
        stance = str(gatekeeper_mercy.get("stance") or "").strip()
        if stance:
            journal_events.append(f"[站队] {speaker_id} -> {stance}")
    gatekeeper_boss_strategy = event.payload.get("gatekeeper_boss_strategy") if isinstance(event.payload, dict) else None
    if isinstance(gatekeeper_boss_strategy, dict) and str(gatekeeper_boss_strategy.get("topic") or "") == "gatekeeper_boss_strategy":
        stance = str(gatekeeper_boss_strategy.get("stance") or "").strip()
        if stance:
            journal_events.append(f"[Boss方案] {speaker_id} -> {stance}")
    return text


def _apply_world_flag_changed(
    *,
    event: DomainEvent,
    flags: Dict[str, Any],
) -> None:
    key = str(event.payload.get("key") or event.payload.get("flag") or "").strip()
    if not key:
        return
    flags[key] = bool(event.payload.get("value", True))


def _apply_actor_affection_changed(
    *,
    event: DomainEvent,
    entities: Dict[str, Dict[str, Any]],
    journal_events: List[str],
) -> None:
    payload = dict(event.payload or {})
    target_actor_id = str(payload.get("target_actor_id") or event.actor_id or "").strip().lower()
    if not target_actor_id:
        return
    entity = entities.get(target_actor_id)
    if not isinstance(entity, dict):
        return
    try:
        delta = int(payload.get("delta") or 0)
    except (TypeError, ValueError):
        delta = 0
    if delta == 0:
        return

    current_affection = int(entity.get("affection") or 0)
    next_affection = max(-100, min(100, current_affection + delta))
    entity["affection"] = next_affection

    dynamic_states = entity.get("dynamic_states")
    if isinstance(dynamic_states, dict):
        affection_state = dynamic_states.get("affection")
        if isinstance(affection_state, dict):
            affection_state["current_value"] = next_affection
            dynamic_states["affection"] = affection_state
            entity["dynamic_states"] = dynamic_states

    reason = str(payload.get("reason") or "").strip()
    reason_suffix = f" ({reason})" if reason else ""
    journal_events.append(
        f"💞 [关系] {target_actor_id} affection {delta:+d} -> {next_affection}{reason_suffix}"
    )


def _build_deterministic_initiative(
    *,
    entities: Dict[str, Dict[str, Any]],
    focus_actor_id: str,
) -> List[str]:
    preferred_order = ["player", "scout", "analyst", "tactician", focus_actor_id]
    seen: set[str] = set()
    ordered: List[str] = []

    for actor_id in preferred_order:
        normalized = str(actor_id or "").strip().lower()
        entity = entities.get(normalized)
        if not normalized or normalized in seen or not isinstance(entity, dict):
            continue
        if str(entity.get("status", "alive")).strip().lower() in {"dead", "downed", "unconscious"}:
            continue
        if entity.get("is_alive") is False:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    for actor_id, entity in entities.items():
        normalized = str(actor_id or "").strip().lower()
        if normalized in seen or not isinstance(entity, dict):
            continue
        if str(entity.get("status", "alive")).strip().lower() in {"dead", "downed", "unconscious"}:
            continue
        if entity.get("is_alive") is False:
            continue
        if str(entity.get("faction", "")).strip().lower() not in {"party", "player", "hostile"}:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    return ordered


def _apply_actor_negotiation_outcome(
    *,
    event: DomainEvent,
    entities: Dict[str, Dict[str, Any]],
    journal_events: List[str],
) -> Dict[str, Any]:
    payload = dict(event.payload or {})
    target_actor_id = str(payload.get("target_actor_id") or "").strip().lower()
    if not target_actor_id:
        return {}
    target = entities.get(target_actor_id)
    if not isinstance(target, dict):
        return {}

    dynamic_states = target.get("dynamic_states")
    if not isinstance(dynamic_states, dict):
        dynamic_states = {}

    patience_state = dynamic_states.get("patience")
    if not isinstance(patience_state, dict):
        patience_state = {"current_value": 0}
    patience_set_raw = payload.get("patience_set")
    if patience_set_raw is not None:
        try:
            patience_state["current_value"] = max(0, int(patience_set_raw))
        except (TypeError, ValueError):
            patience_state["current_value"] = 0
    dynamic_states["patience"] = patience_state

    for state_key, delta_key in (("fear", "fear_delta"), ("paranoia", "paranoia_delta")):
        raw_delta = payload.get(delta_key)
        try:
            delta = int(raw_delta or 0)
        except (TypeError, ValueError):
            delta = 0
        if delta == 0 and state_key != "paranoia":
            continue
        state_payload = dynamic_states.get(state_key)
        if not isinstance(state_payload, dict):
            state_payload = {"current_value": 0}
        current_value = int(state_payload.get("current_value") or 0)
        state_payload["current_value"] = max(0, min(20, current_value + delta))
        dynamic_states[state_key] = state_payload

    target["dynamic_states"] = dynamic_states
    if bool(payload.get("force_hostile", True)):
        target["faction"] = "hostile"

    reason = str(payload.get("reason") or "").strip().lower()
    status_set = str(payload.get("status_set") or "").strip()
    faction_set = str(payload.get("faction_set") or "").strip()
    if reason in {"gatekeeper_mercy_spared", "gatekeeper_mercy_executed"}:
        if status_set:
            target["status"] = status_set
        if faction_set:
            target["faction"] = faction_set
        mercy_state = dynamic_states.get("mercy_window")
        if isinstance(mercy_state, dict):
            mercy_state["current_value"] = False
            dynamic_states["mercy_window"] = mercy_state
        elif "mercy_window" in dynamic_states:
            dynamic_states["mercy_window"] = False
        target["dynamic_states"] = dynamic_states
        if reason == "gatekeeper_mercy_spared":
            journal_events.append("[抉择] gatekeeper -> spared")
        else:
            journal_events.append("[抉择] gatekeeper -> executed")
        return {}
    if reason in {
        "act4_truth_negotiation_success",
        "act4_assault_success",
        "act4_scout_steal_success",
        "act4_scout_steal_failure",
    }:
        if status_set:
            target["status"] = status_set
        if faction_set:
            target["faction"] = faction_set
        if reason == "act4_truth_negotiation_success":
            journal_events.append("[Boss解决] negotiation -> key_surrendered")
        elif reason == "act4_assault_success":
            journal_events.append("[Boss解决] assault -> gatekeeper_defeated")
        return {}
    if reason == "diary_evidence_pressure":
        journal_events.append("[交涉筹码] diary_evidence -> gatekeeper_elixir_truth")
    elif reason == "paranoia_meltdown":
        journal_events.append("💢 [谈判破裂] Gatekeeper 的 paranoia 爆发，认定你们在算计他。")
    else:
        journal_events.append("💢 [谈判破裂] Gatekeeper 被当众激怒，彻底失去耐心。")

    trigger_combat = bool(payload.get("trigger_combat", False))
    if not trigger_combat:
        return {}

    initiative_order = _build_deterministic_initiative(
        entities=entities,
        focus_actor_id=target_actor_id,
    )
    if initiative_order:
        journal_events.append("⚔️ [战斗] 谈判崩塌，战斗立即爆发。")
    return {
        "combat_phase": "IN_COMBAT",
        "combat_active": bool(initiative_order),
        "initiative_order": initiative_order,
        "current_turn_index": 0,
        "turn_resources": {},
    }


def apply_domain_events(state: Dict[str, Any], events: List[DomainEvent]) -> StatePatch:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    flags = dict(state.get("flags") or {})
    messages: List[Any] = []
    speaker_responses: List[Tuple[str, str]] = []
    journal_events: List[str] = []
    reflection_queue = list(state.get("reflection_queue") or [])
    actor_runtime_state = copy.deepcopy(state.get("actor_runtime_state") or {})
    final_response = ""
    combat_phase = None
    combat_active = None
    initiative_order = None
    current_turn_index = None
    turn_resources = None

    for event in events:
        if event.event_type == "actor_spoke":
            latest = _apply_actor_spoke(
                event=event,
                entities=entities,
                environment_objects=environment_objects,
                flags=flags,
                messages=messages,
                speaker_responses=speaker_responses,
                journal_events=journal_events,
            )
            if latest:
                final_response = latest
            continue

        if event.event_type == "world_flag_changed":
            _apply_world_flag_changed(event=event, flags=flags)
            journal_events.append("📜 [系统] 世界标志已更新。")
            continue

        if event.event_type == "actor_reflection_requested":
            reflection_queue.append(
                {
                    "actor_id": event.actor_id,
                    "reason": str(event.payload.get("reason") or "unspecified"),
                    "priority": int(event.payload.get("priority") or 0),
                    "source_turn": int(event.turn_index or 0),
                    "payload": dict(event.payload),
                }
            )
            journal_events.append(f"🧠 [后台] {event.actor_id} 安排了一次反思。")
            continue

        if event.event_type == "actor_belief_updated":
            runtime_state = dict(actor_runtime_state.get(event.actor_id) or {})
            beliefs = list(runtime_state.get("beliefs") or [])
            belief_text = str(event.payload.get("belief") or "").strip()
            if belief_text:
                beliefs.append(belief_text)
                runtime_state["beliefs"] = beliefs[-20:]
            actor_runtime_state[event.actor_id] = runtime_state
            journal_events.append(f"🧠 [认知] {event.actor_id} 的内部信念发生了变化。")
            continue

        if event.event_type == "actor_memory_update_requested":
            payload = dict(event.payload or {})
            scope = str(payload.get("scope") or "actor_private").strip().lower()
            bucket_id = str(event.actor_id or "").strip().lower() or "unknown"
            if scope == "party_shared":
                bucket_id = "__party_shared__"
            elif scope == "world":
                bucket_id = "__world__"

            runtime_state = dict(actor_runtime_state.get(bucket_id) or {})
            memory_notes = list(runtime_state.get("memory_notes") or [])
            memory_text = str(payload.get("text") or "").strip()
            if memory_text:
                memory_notes.append(memory_text)
                runtime_state["memory_notes"] = memory_notes[-20:]
                actor_runtime_state[bucket_id] = runtime_state
            if scope == "party_shared":
                journal_events.append("🧠 [记忆] 队伍共享记忆新增了一条线索。")
            elif scope == "world":
                journal_events.append("🧠 [记忆] 世界记忆新增了一条线索。")
            else:
                journal_events.append(f"🧠 [记忆] {event.actor_id} 记录了一条私有记忆。")
            continue

        if event.event_type == "actor_affection_changed":
            _apply_actor_affection_changed(
                event=event,
                entities=entities,
                journal_events=journal_events,
            )
            continue

        if event.event_type == "actor_negotiation_outcome_requested":
            combat_patch = _apply_actor_negotiation_outcome(
                event=event,
                entities=entities,
                journal_events=journal_events,
            )
            if combat_patch:
                combat_phase = str(combat_patch.get("combat_phase") or "IN_COMBAT")
                combat_active = bool(combat_patch.get("combat_active", False))
                initiative_order = list(combat_patch.get("initiative_order") or [])
                current_turn_index = int(combat_patch.get("current_turn_index") or 0)
                turn_resources = dict(combat_patch.get("turn_resources") or {})
            continue

        if event.event_type == "actor_physical_action_requested":
            journal_events.append(f"⚙️ [行动请求] {event.actor_id} 请求了物理动作结算。")
            continue

        if event.event_type == "actor_item_transaction_requested":
            social_action = social_action_from_payload(
                event.payload.get("social_action"),
                actor_id=event.actor_id,
                reason=str(event.payload.get("reason") or ""),
            )
            transaction = item_transaction_from_payload(
                event.payload.get("transaction"),
                default_reason=social_action.reason if social_action else "",
            )
            if transaction is None:
                journal_events.append(f"❌ [社交物品] {event.actor_id} 的交易事件缺失 transaction payload。")
                continue

        if transaction.accepted and transaction.transaction_type in {"transfer", "return", "consume"}:
            item_transfers = [item_transaction_to_transfer_payload(transaction)]
            hp_changes = list(event.payload.get("hp_changes") or [])
            physics_events = apply_physics(
                entities,
                    player_inventory,
                    item_transfers=item_transfers,
                hp_changes=hp_changes,
            )
            journal_events.extend(physics_events)
            if transaction.item == "heavy_iron_key" and transaction.from_entity == "gatekeeper":
                journal_events.append("[物品转移] gatekeeper -> player heavy_iron_key")
            continue

            action_label = social_action.action_type if social_action else "gift_reject"
            rejected_reason = transaction.reason or "rejected"
            journal_events.append(
                f"🚫 [社交物品] {event.actor_id} {action_label} -> {transaction.item} ({rejected_reason})"
            )
            continue

    return StatePatch(
        entities=entities,
        environment_objects=environment_objects,
        player_inventory=player_inventory,
        flags=flags,
        journal_events=tuple(journal_events),
        messages=tuple(messages),
        speaker_responses=tuple(speaker_responses),
        reflection_queue=tuple(reflection_queue),
        actor_runtime_state=actor_runtime_state,
        pending_events=tuple(),
        combat_phase=combat_phase or "",
        combat_active=combat_active,
        initiative_order=tuple(initiative_order or ()),
        current_turn_index=current_turn_index,
        turn_resources=turn_resources,
        final_response=final_response,
    )
