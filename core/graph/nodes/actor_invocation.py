from __future__ import annotations

from typing import Any, Dict, List

from core.actors.party_turn import collect_party_turn_candidates, run_party_turn
from core.actors.executor import (
    FALLBACK_REASON_RUNTIME_FAILED,
    FALLBACK_REASON_RUNTIME_MISSING,
    enqueue_reflection_requests,
)
from core.actors.registry import ActorRegistry, get_default_actor_registry
from core.eval.telemetry import emit_telemetry
from core.events.models import event_to_dict
from core.events.store import append_pending_events
from core.graph.graph_state import GameState


ACTOR_INVOCATION_MODE_RUNTIME = "runtime"
ACTOR_INVOCATION_MODE_FALLBACK = "fallback"
ACTOR_INVOCATION_MODE_LEGACY = "legacy"
ACTOR_INVOCATION_REASON_RUNTIME_ENABLED = "runtime_enabled"
ACTOR_INVOCATION_REASON_PARTY_TURN_RUNTIME_MULTI = "party_turn_runtime_multi"
PARTY_TURN_FALLBACK_GENERATION_REASON = "party_turn_fallback_generation"
FALLBACK_REASON_ACTOR_ID_MISSING = "actor_id_missing"
_VALID_FALLBACK_REASONS = frozenset(
    {
        FALLBACK_REASON_RUNTIME_MISSING,
        FALLBACK_REASON_RUNTIME_FAILED,
        FALLBACK_REASON_ACTOR_ID_MISSING,
    }
)


def _normalize_fallback_reason(raw_reason: Any) -> str:
    reason = str(raw_reason or "").strip().lower()
    if reason in _VALID_FALLBACK_REASONS:
        return reason
    return FALLBACK_REASON_RUNTIME_FAILED


def _fallback_response(actor_id: str, reason: Any) -> Dict[str, Any]:
    normalized_reason = _normalize_fallback_reason(reason)
    normalized_actor_id = str(actor_id or "").strip().lower() or "unknown"
    emit_telemetry(
        "actor_runtime_decision",
        actor_id=normalized_actor_id,
        mode="fallback",
        reason=normalized_reason,
        decision_kind="fallback",
        emitted_event_count=0,
        reflection_request_count=0,
        duration_ms=0,
    )
    return {
        "actor_invocation_mode": ACTOR_INVOCATION_MODE_FALLBACK,
        "actor_invocation_reason": normalized_reason,
    }


async def actor_invocation_node(
    state: GameState,
    *,
    actor_registry: ActorRegistry | None = None,
) -> Dict[str, Any]:
    actor_id = str(state.get("current_speaker") or "").strip().lower()
    if not actor_id:
        return _fallback_response(actor_id, FALLBACK_REASON_ACTOR_ID_MISSING)

    registry = actor_registry or get_default_actor_registry()
    candidate_actor_ids = collect_party_turn_candidates(dict(state or {}))
    if not candidate_actor_ids:
        return _fallback_response(actor_id, FALLBACK_REASON_ACTOR_ID_MISSING)

    party_turn = await run_party_turn(
        state=dict(state or {}),
        registry=registry,
        candidate_actor_ids=candidate_actor_ids,
    )
    runtime_actor_ids = list(party_turn.get("runtime_actor_ids") or [])
    if not runtime_actor_ids:
        fallback = list(party_turn.get("fallback") or [])
        reason = fallback[0]["reason"] if fallback and isinstance(fallback[0], dict) else FALLBACK_REASON_RUNTIME_FAILED
        return _fallback_response(actor_id, reason)

    decision_metas = list(party_turn.get("decision_metas") or [])
    events = list(party_turn.get("events") or [])
    reflections = list(party_turn.get("reflections") or [])
    fallback = [
        item
        for item in list(party_turn.get("fallback") or [])
        if isinstance(item, dict) and str(item.get("actor_id") or "").strip().lower()
    ]

    if len(runtime_actor_ids) > 1:
        emit_telemetry(
            "actor_runtime_decision",
            actor_id=runtime_actor_ids[0],
            mode="runtime",
            reason=ACTOR_INVOCATION_REASON_PARTY_TURN_RUNTIME_MULTI,
            decision_kind="party_turn",
            emitted_event_count=len(events),
            reflection_request_count=len(reflections),
            duration_ms=0,
        )

    for fallback_item in fallback:
        fallback_actor_id = str(fallback_item.get("actor_id") or "").strip().lower()
        if not fallback_actor_id:
            continue
        emit_telemetry(
            "actor_runtime_decision",
            actor_id=fallback_actor_id,
            mode="legacy",
            reason=PARTY_TURN_FALLBACK_GENERATION_REASON,
            upstream_reason=str(fallback_item.get("reason") or ""),
            decision_kind="fallback_to_generation",
            emitted_event_count=0,
            reflection_request_count=0,
            duration_ms=0,
        )

    event_dicts = [event_to_dict(event) for event in events]
    pending_events = append_pending_events(dict(state or {}), event_dicts)
    reflection_queue = enqueue_reflection_requests(
        state=dict(state or {}),
        requests=reflections,
    )
    invocation_reason = (
        ACTOR_INVOCATION_REASON_PARTY_TURN_RUNTIME_MULTI
        if len(runtime_actor_ids) > 1
        else ACTOR_INVOCATION_REASON_RUNTIME_ENABLED
    )

    out: Dict[str, Any] = {
        "actor_invocation_mode": ACTOR_INVOCATION_MODE_RUNTIME,
        "actor_invocation_reason": invocation_reason,
        "last_actor_decision": decision_metas[-1] if decision_metas else {},
        "pending_events": pending_events,
        "reflection_queue": reflection_queue,
    }
    if len(runtime_actor_ids) > 1:
        out["party_turn_actor_ids"] = runtime_actor_ids
        out["party_turn_decisions"] = decision_metas
    if fallback:
        out["speaker_queue"] = [
            str(item.get("actor_id") or "").strip().lower()
            for item in fallback
            if str(item.get("actor_id") or "").strip().lower()
        ]
    elif len(candidate_actor_ids) > 1:
        out["speaker_queue"] = []
    if reflections:
        emit_telemetry(
            "reflection_enqueued",
            actor_id=runtime_actor_ids[-1],
            count=len(reflections),
            queue_size=len(reflection_queue),
        )
    return out
