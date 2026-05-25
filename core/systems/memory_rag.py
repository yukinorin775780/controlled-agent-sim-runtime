from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from config import settings
from core.memory.chroma_store import ChromaMemoryStore
from core.memory.compat import create_manual_record
from core.memory.distiller import RuleBasedMemoryDistiller
from core.memory.retrieval import ActorScopedMemoryRetriever
from core.memory.service import MemoryService


class EpisodicMemoryManager:
    """
    Compatibility shim for legacy imports.
    New logic is delegated to core.memory.MemoryService.
    """

    def __init__(self, service=None):
        if service is not None:
            self._service = service
            return
        # Legacy shim uses isolated storage to avoid polluting actor-scoped runtime memory.
        legacy_db_path = os.path.join(settings.SAVE_DIR, "chroma_db_legacy")
        store = ChromaMemoryStore(db_path=legacy_db_path)
        self._service = MemoryService(
            store=store,
            retriever=ActorScopedMemoryRetriever(store),
            distiller=RuleBasedMemoryDistiller(),
        )

    def clear_all_memories(self) -> None:
        self._service.clear_all()
        print("💥 [系统] 长期记忆库已清空。")

    def add_memory(self, text: str, speaker: str = "system", metadata: Optional[Dict[str, Any]] = None) -> None:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return

        meta = metadata or {}
        record = create_manual_record(
            text=normalized_text,
            speaker=speaker,
            scope=str(meta.get("scope") or "party_shared"),
            memory_type=str(meta.get("memory_type") or "episodic"),
            importance=int(meta.get("importance") or 1),
            location_id=str(meta.get("location_id") or ""),
            turn_index=int(meta.get("turn") or meta.get("turn_index") or 0),
            source_session_id=str(meta.get("session_id") or ""),
        )
        self._service.upsert(record)
        print(f"🧠 [记忆凝结] {speaker} 的记忆已存入: {normalized_text[:30]}...")

    def retrieve_relevant_memories(
        self,
        query: str,
        top_k: int = 3,
        *,
        actor_id: Optional[str] = None,
        current_location: str = "",
        turn_index: int = 0,
    ) -> List[str]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        def _lexical_score(text: str, q: str) -> int:
            lowered_text = str(text or "").lower()
            lowered_query = str(q or "").lower()
            if not lowered_text or not lowered_query:
                return 0
            # Hybrid token+character overlap for English/Chinese robustness.
            score = 0
            for token in lowered_query.split():
                if token and token in lowered_text:
                    score += 2
            for ch in lowered_query:
                if ch.strip() and ch in lowered_text:
                    score += 1
            return score

        if actor_id:
            actor_texts = self._service.retrieve_texts_for_actor(
                actor_id=str(actor_id),
                query_text=normalized_query,
                current_location=current_location,
                turn_index=turn_index,
                top_k=max(top_k * 5, 10),
            )
            actor_texts.sort(key=lambda item: _lexical_score(item, normalized_query), reverse=True)
            return actor_texts[:top_k]

        snippets = self._service.retrieve_for_director(
            query_text=normalized_query,
            current_location=current_location,
            turn_index=turn_index,
            top_k=max(top_k * 5, 10),
        )
        texts = [snippet.text for snippet in snippets if str(snippet.text or "").strip()]
        texts.sort(key=lambda item: _lexical_score(item, normalized_query), reverse=True)
        return texts[:top_k]


episodic_memory = EpisodicMemoryManager()
