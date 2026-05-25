from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from core.actors.contracts import ReflectionRequest
from core.actors.executor import invoke_actor_runtime
from core.actors.registry import ActorRegistry
from core.events.models import DomainEvent


FALLBACK_REASON_NOT_ELIGIBLE = "not_eligible"


@dataclass(frozen=True)
class PartyTurnFallback:
    actor_id: str
    reason: str


@dataclass(frozen=True)
class PartyTurnRuntimeInvocation:
    actor_id: str
    decision_meta: Dict[str, Any]
    events: Tuple[DomainEvent, ...]
    reflections: Tuple[ReflectionRequest, ...]


def _normalize_actor_id(value: Any) -> str:
    return str(value or "").strip().lower()


def collect_party_turn_candidates(state: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()
    raw_candidates = [state.get("current_speaker")] + list(state.get("speaker_queue") or [])
    for raw_actor_id in raw_candidates:
        actor_id = _normalize_actor_id(raw_actor_id)
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        candidates.append(actor_id)
    return candidates


def _is_actor_eligible_for_runtime(state: Dict[str, Any], actor_id: str) -> bool:
    entities = state.get("entities")
    if not isinstance(entities, dict):
        return True
    entity = entities.get(actor_id)
    if not isinstance(entity, dict):
        return True

    status = str(entity.get("status", "alive") or "").strip().lower()
    if status in {"dead", "downed", "unconscious"}:
        return False
    if entity.get("is_alive") is False:
        return False
    return True


async def run_party_turn(
    *,
    state: Dict[str, Any],
    registry: ActorRegistry,
    candidate_actor_ids: List[str],
) -> Dict[str, Any]:
    runtime_invocations: List[PartyTurnRuntimeInvocation] = []
    fallback: List[PartyTurnFallback] = []

    for actor_id in candidate_actor_ids:
        if not _is_actor_eligible_for_runtime(state, actor_id):
            fallback.append(PartyTurnFallback(actor_id=actor_id, reason=FALLBACK_REASON_NOT_ELIGIBLE))
            continue

        decision_meta, events, reflections = await invoke_actor_runtime(
            state=dict(state or {}),
            actor_id=actor_id,
            registry=registry,
        )
        if str(decision_meta.get("mode") or "") == "fallback":
            fallback.append(
                PartyTurnFallback(
                    actor_id=actor_id,
                    reason=str(decision_meta.get("reason") or "runtime_failed"),
                )
            )
            continue
        runtime_invocations.append(
            PartyTurnRuntimeInvocation(
                actor_id=actor_id,
                decision_meta=dict(decision_meta),
                events=tuple(events),
                reflections=tuple(reflections),
            )
        )

    merged_events: List[DomainEvent] = []
    merged_reflections: List[ReflectionRequest] = []
    decision_metas: List[Dict[str, Any]] = []
    runtime_actor_ids: List[str] = []
    for item in runtime_invocations:
        runtime_actor_ids.append(item.actor_id)
        decision_metas.append(dict(item.decision_meta))
        merged_events.extend(item.events)
        merged_reflections.extend(item.reflections)

    return {
        "runtime_actor_ids": runtime_actor_ids,
        "decision_metas": decision_metas,
        "events": merged_events,
        "reflections": merged_reflections,
        "fallback": [{"actor_id": item.actor_id, "reason": item.reason} for item in fallback],
    }
