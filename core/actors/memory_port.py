from __future__ import annotations

from typing import List, Protocol

from core.memory.models import MemoryQuery
from core.memory.protocols import MemoryRetriever


class MemorySnippetProvider(Protocol):
    def retrieve_for_actor(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 2,
        current_location: str = "",
        turn_index: int = 0,
    ) -> List[str]:
        ...


class GlobalMemoryAdapter:
    """
    Legacy adapter.
    Phase 2 keeps this for backward compatibility with global episodic memory calls.
    """

    def __init__(self, episodic_memory):
        self._episodic_memory = episodic_memory

    def retrieve_for_actor(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 2,
        current_location: str = "",
        turn_index: int = 0,
    ) -> List[str]:
        del actor_id, current_location, turn_index  # legacy path ignores scoped context.
        return self._episodic_memory.retrieve_relevant_memories(query, top_k=top_k)


class ActorScopedMemoryProvider:
    """
    Phase 2 adapter: ActorView memory snippets now come from actor-scoped retriever.
    """

    def __init__(self, retriever: MemoryRetriever):
        self._retriever = retriever

    def retrieve_for_actor(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 2,
        current_location: str = "",
        turn_index: int = 0,
    ) -> List[str]:
        snippets = self._retriever.retrieve_for_actor(
            MemoryQuery(
                actor_id=actor_id,
                query_text=query,
                current_location=current_location,
                turn_index=int(turn_index or 0),
                top_k=top_k,
            )
        )
        return [snippet.text for snippet in snippets if str(snippet.text or "").strip()]
