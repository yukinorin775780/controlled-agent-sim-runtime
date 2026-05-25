import asyncio

from core.actors.contracts import ActorDecision, ReflectionRequest
from core.events.models import DomainEvent


class FakeActorRuntime:
    async def decide(self, actor_view):
        _ = actor_view
        return ActorDecision(
            actor_id="analyst",
            kind="speak",
            spoken_text="……我明白了。",
            thought_summary="她仍保持警惕。",
            emitted_events=(),
            requested_reflections=(),
        )

    async def reflect(self, request):
        return (
            DomainEvent(
                event_id="evt-1",
                event_type="actor_belief_updated",
                actor_id="analyst",
                turn_index=request.source_turn,
                visibility="private",
                payload={"belief": "player_is_reliable"},
            ),
        )


def test_actor_runtime_decide_returns_actor_decision_only():
    runtime = FakeActorRuntime()

    result = asyncio.run(runtime.decide(actor_view=object()))

    assert isinstance(result, ActorDecision)
    assert result.actor_id == "analyst"
    assert result.kind == "speak"
    assert result.spoken_text == "……我明白了。"


def test_actor_runtime_reflect_returns_events_not_state():
    runtime = FakeActorRuntime()
    request = ReflectionRequest(
        actor_id="analyst",
        reason="post_dialogue_reflection",
        priority=10,
        source_turn=12,
        payload={},
    )

    events = asyncio.run(runtime.reflect(request))

    assert len(events) == 1
    assert isinstance(events[0], DomainEvent)
    assert events[0].event_type == "actor_belief_updated"
    assert events[0].visibility == "private"
