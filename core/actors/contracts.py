from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Optional, Protocol, Tuple

from core.events.models import DomainEvent


ActorDecisionKind = Literal[
    "speak",
    "narrate_reaction",
    "physical_action",
    "silent",
    "schedule_reflection",
]


@dataclass(frozen=True)
class ReflectionRequest:
    actor_id: str
    reason: str
    priority: int
    source_turn: int
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActorDecision:
    actor_id: str
    kind: ActorDecisionKind
    spoken_text: str = ""
    thought_summary: str = ""
    physical_action: Optional[Dict[str, Any]] = None
    emitted_events: Tuple[DomainEvent, ...] = ()
    requested_reflections: Tuple[ReflectionRequest, ...] = ()


@dataclass(frozen=True)
class StatePatch:
    entities: Optional[Dict[str, Any]] = None
    environment_objects: Optional[Dict[str, Any]] = None
    player_inventory: Optional[Dict[str, Any]] = None
    flags: Optional[Dict[str, Any]] = None
    journal_events: Tuple[str, ...] = ()
    messages: Tuple[Any, ...] = ()
    speaker_responses: Tuple[Tuple[str, str], ...] = ()
    reflection_queue: Tuple[Dict[str, Any], ...] = ()
    actor_runtime_state: Optional[Dict[str, Dict[str, Any]]] = None
    pending_events: Tuple[Dict[str, Any], ...] = ()
    combat_phase: str = ""
    combat_active: Optional[bool] = None
    initiative_order: Tuple[str, ...] = ()
    current_turn_index: Optional[int] = None
    turn_resources: Optional[Dict[str, Dict[str, Any]]] = None
    final_response: str = ""


class ActorRuntime(Protocol):
    async def decide(self, actor_view: "ActorView") -> ActorDecision:
        ...

    async def reflect(self, request: ReflectionRequest) -> Tuple[DomainEvent, ...]:
        ...


def reflection_request_to_dict(request: ReflectionRequest) -> Dict[str, Any]:
    return asdict(request)


def reflection_request_from_dict(payload: Dict[str, Any]) -> ReflectionRequest:
    return ReflectionRequest(
        actor_id=str(payload.get("actor_id") or ""),
        reason=str(payload.get("reason") or ""),
        priority=int(payload.get("priority") or 0),
        source_turn=int(payload.get("source_turn") or 0),
        payload=dict(payload.get("payload") or {}),
    )


def actor_decision_to_dict(decision: ActorDecision) -> Dict[str, Any]:
    return {
        "actor_id": decision.actor_id,
        "kind": decision.kind,
        "spoken_text": decision.spoken_text,
        "thought_summary": decision.thought_summary,
        "physical_action": dict(decision.physical_action or {}) if decision.physical_action else None,
        "emitted_events": [asdict(event) for event in decision.emitted_events],
        "requested_reflections": [asdict(item) for item in decision.requested_reflections],
    }
