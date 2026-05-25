from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from config import settings
from core.memory.models import MemoryQuery, MemoryRecord, MemoryScope, MemorySnippet
from core.memory.protocols import MemoryStore

try:
    import chromadb
except Exception:  # pragma: no cover - optional dependency fallback
    chromadb = None  # type: ignore[assignment]


def scope_to_scope_key(scope: MemoryScope, owner_actor_id: Optional[str]) -> str:
    if scope == "actor_private":
        normalized_actor_id = str(owner_actor_id or "").strip().lower()
        if not normalized_actor_id:
            normalized_actor_id = "unknown"
        return f"actor_private:{normalized_actor_id}"
    return scope


def scope_key_to_collection_name(scope_key: str) -> str:
    if scope_key == "world":
        return "casr_mem_world"
    if scope_key == "party_shared":
        return "casr_mem_party_shared"
    if scope_key.startswith("actor_private:"):
        actor_id = scope_key.split(":", 1)[1].strip().lower() or "unknown"
        return f"casr_mem_actor_{actor_id}"
    return "casr_mem_misc"


class _InMemoryStore(MemoryStore):
    """Fallback store used when chromadb is unavailable."""

    def __init__(self) -> None:
        self._records: Dict[str, List[MemoryRecord]] = {}

    def upsert(self, record: MemoryRecord) -> None:
        scope_key = scope_to_scope_key(record.scope, record.owner_actor_id)
        bucket = self._records.setdefault(scope_key, [])
        for idx, item in enumerate(bucket):
            if item.memory_id == record.memory_id:
                bucket[idx] = record
                break
        else:
            bucket.append(record)

    def query_scope(self, *, scope_key: str, query: MemoryQuery, top_k: int) -> List[MemorySnippet]:
        query_text = str(query.query_text or "").strip().lower()
        if not query_text:
            return []
        candidates = self._records.get(scope_key, [])
        ranked: List[MemorySnippet] = []
        for record in candidates:
            text = str(record.text or "")
            lowered = text.lower()
            overlap = 0
            for token in query_text.split():
                if token and token in lowered:
                    overlap += 1
            if overlap <= 0:
                continue
            ranked.append(
                MemorySnippet(
                    memory_id=record.memory_id,
                    text=text,
                    scope=record.scope,
                    score=float(overlap),
                    memory_type=record.memory_type,
                )
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[: max(0, int(top_k))]

    def clear(self) -> None:
        self._records.clear()


class ChromaMemoryStore(MemoryStore):
    def __init__(self, db_path: Optional[str] = None, client: Optional[Any] = None) -> None:
        if client is not None:
            self._fallback_store = None
            self._client = client
            self._collections: Dict[str, Any] = {}
            return

        if chromadb is None:
            self._fallback_store: Optional[_InMemoryStore] = _InMemoryStore()
            self._client = None
            self._collections: Dict[str, Any] = {}
            return

        self._fallback_store = None
        resolved_db_path = db_path or os.path.join(settings.SAVE_DIR, "chroma_db")
        os.makedirs(resolved_db_path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=resolved_db_path)
        self._collections: Dict[str, Any] = {}

    def _get_collection(self, scope_key: str) -> Any:
        if self._fallback_store is not None:
            return None
        cached = self._collections.get(scope_key)
        if cached is not None:
            return cached
        collection_name = scope_key_to_collection_name(scope_key)
        collection = self._client.get_or_create_collection(name=collection_name)
        self._collections[scope_key] = collection
        return collection

    @staticmethod
    def _record_to_metadata(record: MemoryRecord) -> Dict[str, Any]:
        payload = asdict(record)
        payload["participants"] = list(record.participants)
        payload["tags"] = list(record.tags)
        payload["source_event_ids"] = list(record.source_event_ids)
        return ChromaMemoryStore._sanitize_metadata(payload)

    @staticmethod
    def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize MemoryRecord metadata for ChromaDB's stricter validators."""
        sanitized: Dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                cleaned_items = [
                    item
                    for item in value
                    if item is not None and not (isinstance(item, str) and not item.strip())
                ]
                if not cleaned_items:
                    continue
                sanitized[key] = cleaned_items
                continue
            sanitized[key] = value
        return sanitized

    def upsert(self, record: MemoryRecord) -> None:
        if self._fallback_store is not None:
            self._fallback_store.upsert(record)
            return

        scope_key = scope_to_scope_key(record.scope, record.owner_actor_id)
        collection = self._get_collection(scope_key)
        metadata = self._record_to_metadata(record)
        payload = {
            "ids": [record.memory_id],
            "documents": [record.text],
            "metadatas": [metadata],
        }
        if hasattr(collection, "upsert"):
            collection.upsert(**payload)
            return
        # Older chromadb versions do not expose upsert on collection.
        try:
            collection.add(**payload)
        except Exception:
            if hasattr(collection, "update"):
                collection.update(**payload)
            else:
                raise

    @staticmethod
    def _extract_score(raw_distance: Any) -> float:
        try:
            distance = float(raw_distance)
        except (TypeError, ValueError):
            return 0.0
        # Chroma distance: lower is better. Convert to higher-is-better score.
        return max(0.0, 1.0 - distance)

    def query_scope(self, *, scope_key: str, query: MemoryQuery, top_k: int) -> List[MemorySnippet]:
        if self._fallback_store is not None:
            return self._fallback_store.query_scope(scope_key=scope_key, query=query, top_k=top_k)

        query_text = str(query.query_text or "").strip()
        if not query_text:
            return []

        collection = self._get_collection(scope_key)
        try:
            count = int(collection.count())
        except Exception:
            count = 0
        if count <= 0:
            return []

        requested = min(max(1, int(top_k)), count)
        try:
            result = collection.query(
                query_texts=[query_text],
                n_results=requested,
                include=["documents", "metadatas", "distances"],
            )
        except TypeError:
            # Backward compatibility for older chromadb API.
            result = collection.query(
                query_texts=[query_text],
                n_results=requested,
            )

        documents = ((result or {}).get("documents") or [[]])[0]
        metadatas = ((result or {}).get("metadatas") or [[]])[0]
        distances = ((result or {}).get("distances") or [[]])[0]
        snippets: List[MemorySnippet] = []
        for idx, text in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            scope = str(metadata.get("scope", "world") or "world")
            if scope not in {"world", "party_shared", "actor_private"}:
                scope = "world"
            memory_type = str(metadata.get("memory_type", "episodic") or "episodic")
            if memory_type not in {"episodic", "relationship", "belief", "quest", "combat", "lore"}:
                memory_type = "episodic"
            snippets.append(
                MemorySnippet(
                    memory_id=str(metadata.get("memory_id", "")) or f"{scope_key}:{idx}",
                    text=str(text or ""),
                    scope=scope,  # type: ignore[arg-type]
                    score=self._extract_score(distances[idx] if idx < len(distances) else 1.0),
                    memory_type=memory_type,  # type: ignore[arg-type]
                )
            )
        return snippets

    def clear(self) -> None:
        if self._fallback_store is not None:
            self._fallback_store.clear()
            return

        names = {scope_key_to_collection_name(scope_key) for scope_key in self._collections.keys()}
        try:
            listed = self._client.list_collections()
        except Exception:
            listed = []
        for collection in listed or []:
            name = getattr(collection, "name", None) or str(collection or "")
            if name.startswith("casr_mem_"):
                names.add(name)
        for name in names:
            try:
                self._client.delete_collection(name)
            except Exception:
                continue
        self._collections.clear()
        self._collections.clear()
