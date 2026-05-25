from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple


MemoryScope = Literal["world", "party_shared", "actor_private"]
MemoryType = Literal["episodic", "relationship", "belief", "quest", "combat", "lore"]


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    text: str
    scope: MemoryScope
    memory_type: MemoryType
    owner_actor_id: Optional[str]
    participants: Tuple[str, ...]
    location_id: str
    turn_index: int
    importance: int
    tags: Tuple[str, ...] = ()
    source_event_ids: Tuple[str, ...] = ()
    source_session_id: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class MemorySnippet:
    memory_id: str
    text: str
    scope: MemoryScope
    score: float
    memory_type: MemoryType


@dataclass(frozen=True)
class MemoryQuery:
    actor_id: str
    query_text: str
    current_location: str
    turn_index: int
    top_k: int = 5


@dataclass(frozen=True)
class TurnMemoryInput:
    session_id: str
    user_input: str
    responses: List[Dict[str, str]]
    journal_events: List[str]
    current_location: str
    turn_index: int
    party_status: Dict[str, Dict]
    flags: Dict[str, bool]

