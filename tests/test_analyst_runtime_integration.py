import asyncio
from unittest.mock import AsyncMock, Mock, patch

from core.actors.contracts import ActorDecision
from core.graph.nodes.actor_invocation import actor_invocation_node


def test_analyst_runtime_path_produces_same_external_shape():
    state = {
        "current_speaker": "analyst",
        "user_input": "你信任我吗？",
        "entities": {"analyst": {"hp": 10}},
        "pending_events": [],
    }

    fake_runtime = AsyncMock()
    fake_runtime.decide.return_value = ActorDecision(
        actor_id="analyst",
        kind="speak",
        spoken_text="信任？那得看你值不值得。",
        thought_summary="她保持戒备。",
        emitted_events=(),
        requested_reflections=(),
    )

    registry = Mock()
    registry.try_get.return_value = fake_runtime

    fake_memory_service = Mock()
    fake_memory_service.retriever = Mock()

    with patch("core.actors.executor.build_actor_view", return_value=object()), patch(
        "core.actors.executor.get_default_memory_service",
        return_value=fake_memory_service,
    ):
        result = asyncio.run(actor_invocation_node(state, actor_registry=registry))

    assert result["actor_invocation_mode"] == "runtime"
    assert result["actor_invocation_reason"] == "runtime_enabled"
    assert result["last_actor_decision"]["actor_id"] == "analyst"
    assert result["last_actor_decision"]["kind"] == "speak"
    assert result["last_actor_decision"]["spoken_text"] == "信任？那得看你值不值得。"
