from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from core.memory.chroma_store import ChromaMemoryStore
from core.memory.distiller import RuleBasedMemoryDistiller
from core.memory.models import MemoryRecord
from core.memory.retrieval import ActorScopedMemoryRetriever
from core.memory.service import MemoryService

_DEFAULT_MEMORY_SERVICE: Optional[MemoryService] = None


def _build_default_memory_service() -> MemoryService:
    store = ChromaMemoryStore()
    retriever = ActorScopedMemoryRetriever(store)
    distiller = RuleBasedMemoryDistiller()
    return MemoryService(store=store, retriever=retriever, distiller=distiller)


def get_default_memory_service() -> MemoryService:
    global _DEFAULT_MEMORY_SERVICE
    if _DEFAULT_MEMORY_SERVICE is None:
        _DEFAULT_MEMORY_SERVICE = _build_default_memory_service()
    return _DEFAULT_MEMORY_SERVICE


def reset_default_memory_service() -> None:
    global _DEFAULT_MEMORY_SERVICE
    _DEFAULT_MEMORY_SERVICE = None


def create_manual_record(
    *,
    text: str,
    speaker: str = "system",
    scope: str = "party_shared",
    memory_type: str = "episodic",
    importance: int = 1,
    location_id: str = "",
    turn_index: int = 0,
    source_session_id: str = "",
) -> MemoryRecord:
    normalized_scope = str(scope or "party_shared").strip().lower()
    if normalized_scope not in {"world", "party_shared", "actor_private"}:
        normalized_scope = "party_shared"
    normalized_type = str(memory_type or "episodic").strip().lower()
    if normalized_type not in {"episodic", "relationship", "belief", "quest", "combat", "lore"}:
        normalized_type = "episodic"
    speaker_id = str(speaker or "").strip().lower()
    owner_actor_id = speaker_id if normalized_scope == "actor_private" and speaker_id else None
    return MemoryRecord(
        memory_id=f"mem_{uuid4().hex}",
        text=str(text or "").strip(),
        scope=normalized_scope,  # type: ignore[arg-type]
        memory_type=normalized_type,  # type: ignore[arg-type]
        owner_actor_id=owner_actor_id,
        participants=tuple([speaker_id] if speaker_id else []),
        location_id=str(location_id or ""),
        turn_index=int(turn_index or 0),
        importance=max(1, int(importance)),
        source_session_id=str(source_session_id or ""),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

