from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.memory.models import MemoryQuery, MemoryRecord, MemorySnippet, TurnMemoryInput
from core.memory.protocols import MemoryDistiller, MemoryRetriever, MemoryStore


@dataclass
class MemoryService:
    store: MemoryStore
    retriever: MemoryRetriever
    distiller: MemoryDistiller

    def upsert(self, record: MemoryRecord) -> None:
        self.store.upsert(record)

    def ingest_turn(self, turn_input: TurnMemoryInput) -> List[MemoryRecord]:
        records = self.distiller.distill_turn(turn_input)
        for record in records:
            self.store.upsert(record)
        return records

    def retrieve_for_actor(
        self,
        *,
        actor_id: str,
        query_text: str,
        current_location: str,
        turn_index: int,
        top_k: int = 5,
    ) -> List[MemorySnippet]:
        query = MemoryQuery(
            actor_id=actor_id,
            query_text=query_text,
            current_location=current_location,
            turn_index=turn_index,
            top_k=top_k,
        )
        return self.retriever.retrieve_for_actor(query)

    def retrieve_for_director(
        self,
        *,
        query_text: str,
        current_location: str,
        turn_index: int,
        top_k: int = 5,
    ) -> List[MemorySnippet]:
        query = MemoryQuery(
            actor_id="director",
            query_text=query_text,
            current_location=current_location,
            turn_index=turn_index,
            top_k=top_k,
        )
        return self.retriever.retrieve_for_director(query)

    def retrieve_texts_for_actor(
        self,
        *,
        actor_id: str,
        query_text: str,
        current_location: str,
        turn_index: int,
        top_k: int = 5,
    ) -> List[str]:
        return [
            snippet.text
            for snippet in self.retrieve_for_actor(
                actor_id=actor_id,
                query_text=query_text,
                current_location=current_location,
                turn_index=turn_index,
                top_k=top_k,
            )
            if str(snippet.text or "").strip()
        ]

    def clear_all(self) -> None:
        self.store.clear()

