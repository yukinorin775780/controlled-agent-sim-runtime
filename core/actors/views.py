from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class VisibleMessage:
    role: str
    speaker_id: str
    content: str


@dataclass(frozen=True)
class ActorSelfState:
    actor_id: str
    name: str
    hp: int
    max_hp: int
    inventory: Dict[str, int]
    affection: int
    active_buffs: List[Dict[str, Any]]
    position: str
    dynamic_states: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicEntityView:
    entity_id: str
    name: str
    position: str
    status: str
    faction: str
    entity_type: str = ""
    is_party_member: bool = False


@dataclass(frozen=True)
class ActorView:
    actor_id: str
    user_input: str
    intent: str
    intent_context: Dict[str, Any]
    is_probing_secret: bool

    self_state: ActorSelfState
    other_entities: Dict[str, PublicEntityView]

    current_location: str
    time_of_day: str
    turn_count: int
    visible_environment_objects: Dict[str, Dict[str, Any]]
    visible_flags: Dict[str, bool]

    visible_history: List[VisibleMessage]
    recent_public_events: List[str]
    latest_roll: Dict[str, Any]

    memory_snippets: List[str]


@dataclass(frozen=True)
class DirectorView:
    """Phase 1 placeholder for orchestration authority."""

    state: Dict[str, Any] = field(default_factory=dict)

