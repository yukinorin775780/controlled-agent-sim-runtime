from __future__ import annotations

from typing import List, Protocol

from core.memory.models import MemoryQuery, MemoryRecord, MemorySnippet, TurnMemoryInput


class MemoryStore(Protocol):
    def upsert(self, record: MemoryRecord) -> None:
        ...

    def query_scope(self, *, scope_key: str, query: MemoryQuery, top_k: int) -> List[MemorySnippet]:
        ...

    def clear(self) -> None:
        ...


class MemoryRetriever(Protocol):
    def retrieve_for_actor(self, query: MemoryQuery) -> List[MemorySnippet]:
        ...

    def retrieve_for_director(self, query: MemoryQuery) -> List[MemorySnippet]:
        ...


class MemoryDistiller(Protocol):
    def distill_turn(self, turn_input: TurnMemoryInput) -> List[MemoryRecord]:
        ...

