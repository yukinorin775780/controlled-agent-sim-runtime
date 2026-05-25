from core.memory.chroma_store import ChromaMemoryStore
from core.memory.models import MemoryQuery, MemoryRecord


class FakeCollection:
    def __init__(self):
        self.add_calls = []
        self.query_calls = []

    def count(self):
        return 1

    def add(self, documents, metadatas, ids):
        self.add_calls.append((documents, metadatas, ids))

    def query(self, query_texts, n_results, include=None):
        self.query_calls.append((query_texts, n_results, include))
        return {
            "documents": [["记忆A"]],
            "metadatas": [[{"memory_id": "m1", "scope": "world", "memory_type": "episodic"}]],
            "distances": [[0.2]],
        }


class FakeClient:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name):
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]

    def delete_collection(self, name):
        self.collections.pop(name, None)


def test_store_writes_world_record_to_world_collection():
    client = FakeClient()
    store = ChromaMemoryStore(client=client)

    record = MemoryRecord(
        memory_id="m1",
        text="世界线推进",
        scope="world",
        memory_type="quest",
        owner_actor_id=None,
        participants=("player", "analyst"),
        location_id="camp_fire",
        turn_index=10,
        importance=3,
    )

    store.upsert(record)

    assert "casr_mem_world" in client.collections
    collection = client.collections["casr_mem_world"]
    assert len(collection.add_calls) == 1


def test_store_omits_empty_list_metadata_for_chroma_compatibility():
    client = FakeClient()
    store = ChromaMemoryStore(client=client)

    record = MemoryRecord(
        memory_id="m_empty_lists",
        text="分析员记得夜兰花",
        scope="party_shared",
        memory_type="episodic",
        owner_actor_id=None,
        participants=("analyst",),
        location_id="camp_fire",
        turn_index=1,
        importance=2,
        tags=(),
        source_event_ids=(),
    )

    store.upsert(record)

    collection = client.collections["casr_mem_party_shared"]
    _, metadatas, _ = collection.add_calls[0]
    metadata = metadatas[0]
    assert metadata["participants"] == ["analyst"]
    assert "tags" not in metadata
    assert "source_event_ids" not in metadata
    assert "owner_actor_id" not in metadata


def test_store_queries_private_scope_from_private_collection():
    client = FakeClient()
    store = ChromaMemoryStore(client=client)

    query = MemoryQuery(
        actor_id="analyst",
        query_text="artifact",
        current_location="camp_fire",
        turn_index=12,
        top_k=3,
    )

    store.query_scope(scope_key="actor_private:analyst", query=query, top_k=3)

    assert "casr_mem_actor_analyst" in client.collections
    collection = client.collections["casr_mem_actor_analyst"]
    assert collection.query_calls == [
        (
            ["artifact"],
            1,
            ["documents", "metadatas", "distances"],
        )
    ]
