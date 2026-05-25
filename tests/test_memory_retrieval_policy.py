from core.memory.models import MemoryQuery, MemorySnippet
from core.memory.retrieval import ActorScopedMemoryRetriever


class FakeStore:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def upsert(self, record):
        _ = record

    def clear(self):
        self.responses.clear()

    def query_scope(self, *, scope_key, query, top_k):
        self.calls.append((scope_key, query.actor_id, query.query_text, top_k))
        return list(self.responses.get(scope_key, []))


def test_actor_retriever_queries_private_shared_world_in_order():
    store = FakeStore(
        {
            "actor_private:analyst": [],
            "party_shared": [],
            "world": [],
        }
    )
    retriever = ActorScopedMemoryRetriever(store)

    retriever.retrieve_for_actor(
        MemoryQuery(
            actor_id="analyst",
            query_text="artifact",
            current_location="camp",
            turn_index=10,
            top_k=5,
        )
    )

    assert store.calls == [
        ("actor_private:analyst", "analyst", "artifact", 3),
        ("party_shared", "analyst", "artifact", 2),
        ("world", "analyst", "artifact", 2),
    ]


def test_actor_retriever_deduplicates_and_reranks_results():
    shared = MemorySnippet("m2", "共同经历", "party_shared", 0.75, "episodic")
    private = MemorySnippet("m1", "她记得秘密", "actor_private", 0.60, "belief")
    duplicate = MemorySnippet("m2", "共同经历", "world", 0.70, "episodic")

    store = FakeStore(
        {
            "actor_private:analyst": [private],
            "party_shared": [shared],
            "world": [duplicate],
        }
    )
    retriever = ActorScopedMemoryRetriever(store)

    results = retriever.retrieve_for_actor(
        MemoryQuery(
            actor_id="analyst",
            query_text="artifact",
            current_location="camp",
            turn_index=10,
            top_k=5,
        )
    )

    assert [item.memory_id for item in results] == ["m2", "m1"]


def test_director_retriever_never_queries_actor_private_scope():
    store = FakeStore({"party_shared": [], "world": []})
    retriever = ActorScopedMemoryRetriever(store)

    retriever.retrieve_for_director(
        MemoryQuery(
            actor_id="director",
            query_text="artifact",
            current_location="camp",
            turn_index=10,
            top_k=5,
        )
    )

    assert all(not scope.startswith("actor_private:") for scope, *_ in store.calls)
