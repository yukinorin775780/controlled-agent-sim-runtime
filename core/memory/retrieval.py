from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from core.eval.telemetry import emit_telemetry
from core.memory.models import MemoryQuery, MemorySnippet
from core.memory.protocols import MemoryRetriever, MemoryStore


def _scope_key_actor_private(actor_id: str) -> str:
    normalized = str(actor_id or "").strip().lower() or "unknown"
    return f"actor_private:{normalized}"


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


@dataclass(frozen=True)
class _ScoredSnippet:
    snippet: MemorySnippet
    final_score: float


class ActorScopedMemoryRetriever(MemoryRetriever):
    """Policy retriever with strict actor-private isolation."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @staticmethod
    def _importance_bonus(snippet: MemorySnippet) -> float:
        # Phase 2 V1: if metadata importance is unavailable in snippet, keep neutral.
        return 0.0

    @staticmethod
    def _recency_bonus(query: MemoryQuery) -> float:
        # Phase 2 V1: recency requires per-snippet turn metadata, currently unavailable.
        _ = query
        return 0.0

    @staticmethod
    def _location_bonus(query: MemoryQuery, snippet: MemorySnippet) -> float:
        _ = (query, snippet)
        return 0.0

    def _rescore(self, *, query: MemoryQuery, snippets: List[MemorySnippet]) -> List[_ScoredSnippet]:
        rescored: List[_ScoredSnippet] = []
        for snippet in snippets:
            final_score = (
                float(snippet.score)
                + self._importance_bonus(snippet)
                + self._recency_bonus(query)
                + self._location_bonus(query, snippet)
            )
            rescored.append(_ScoredSnippet(snippet=snippet, final_score=final_score))
        rescored.sort(key=lambda item: item.final_score, reverse=True)
        return rescored

    @staticmethod
    def _dedupe(scored: List[_ScoredSnippet], top_k: int) -> List[MemorySnippet]:
        seen: Set[str] = set()
        out: List[MemorySnippet] = []
        for item in scored:
            memory_id = str(item.snippet.memory_id or "").strip()
            key = memory_id or _normalize_text(item.snippet.text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item.snippet)
            if len(out) >= top_k:
                break
        return out

    def retrieve_for_actor(self, query: MemoryQuery) -> List[MemorySnippet]:
        actor_scope_key = _scope_key_actor_private(query.actor_id)
        actor_snippets = self._store.query_scope(scope_key=actor_scope_key, query=query, top_k=3)
        party_snippets = self._store.query_scope(scope_key="party_shared", query=query, top_k=2)
        world_snippets = self._store.query_scope(scope_key="world", query=query, top_k=2)
        all_snippets = actor_snippets + party_snippets + world_snippets
        rescored = self._rescore(query=query, snippets=all_snippets)
        result = self._dedupe(rescored, top_k=max(1, int(query.top_k)))
        emit_telemetry(
            "memory_retrieval",
            mode="actor",
            actor_id=str(query.actor_id or "").strip().lower(),
            query_text_length=len(str(query.query_text or "")),
            top_k=int(query.top_k or 0),
            hit_count=len(result),
            scope_hits={
                "actor_private": len(actor_snippets),
                "party_shared": len(party_snippets),
                "world": len(world_snippets),
            },
        )
        return result

    def retrieve_for_director(self, query: MemoryQuery) -> List[MemorySnippet]:
        # Director can only see world + party_shared.
        party_snippets = self._store.query_scope(scope_key="party_shared", query=query, top_k=3)
        world_snippets = self._store.query_scope(scope_key="world", query=query, top_k=3)
        rescored = self._rescore(query=query, snippets=party_snippets + world_snippets)
        result = self._dedupe(rescored, top_k=max(1, int(query.top_k)))
        emit_telemetry(
            "memory_retrieval",
            mode="director",
            actor_id=str(query.actor_id or "").strip().lower(),
            query_text_length=len(str(query.query_text or "")),
            top_k=int(query.top_k or 0),
            hit_count=len(result),
            scope_hits={
                "party_shared": len(party_snippets),
                "world": len(world_snippets),
            },
        )
        return result
