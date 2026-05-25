from core.actors.memory_port import ActorScopedMemoryProvider
from core.memory.models import MemoryQuery, MemorySnippet


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def retrieve_for_actor(self, query: MemoryQuery):
        self.calls.append(query)
        return [
            MemorySnippet("m1", "她记得神器的秘密。", "actor_private", 0.91, "belief"),
            MemorySnippet("m2", "队伍曾共同逃出飞船。", "party_shared", 0.80, "episodic"),
        ]

    def retrieve_for_director(self, query: MemoryQuery):
        _ = query
        return []


def test_actor_memory_provider_returns_plain_text_snippets():
    retriever = FakeRetriever()
    provider = ActorScopedMemoryProvider(retriever)

    snippets = provider.retrieve_for_actor(
        actor_id="analyst",
        query="artifact",
        top_k=2,
        current_location="camp_fire",
        turn_index=12,
    )

    assert snippets == [
        "她记得神器的秘密。",
        "队伍曾共同逃出飞船。",
    ]
    assert retriever.calls[0].actor_id == "analyst"
    assert retriever.calls[0].query_text == "artifact"
    assert retriever.calls[0].current_location == "camp_fire"
    assert retriever.calls[0].turn_index == 12
