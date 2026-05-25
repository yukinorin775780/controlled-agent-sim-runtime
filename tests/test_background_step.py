import asyncio
from unittest.mock import AsyncMock, Mock, patch

from core.application.game_service import GameService
from core.eval.telemetry import InMemoryTelemetrySink, telemetry_scope


class _AsyncContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class _FakeGraph:
    def __init__(self, state):
        self.state = state
        self.ainvoke_calls = []
        self.aupdate_calls = []

    async def aget_state(self, config):
        _ = config

        class Snapshot:
            values = self.state

        return Snapshot()

    async def aupdate_state(self, config, payload, as_node):
        _ = (config, as_node)
        self.aupdate_calls.append(dict(payload))
        self.state.update(payload)

    async def ainvoke(self, payload, config):
        _ = config
        self.ainvoke_calls.append(dict(payload))
        return self.state


def test_background_step_only_processes_reflection_path():
    state = {
        "entities": {"analyst": {"hp": 10, "faction": "party"}},
        "journal_events": [],
        "speaker_responses": [],
        "messages": [],
        "pending_events": [],
        "reflection_queue": [],
    }
    graph = _FakeGraph(state)

    service = GameService(
        saver_factory=Mock(return_value=_AsyncContextManager(object())),
        graph_builder=Mock(return_value=graph),
        initial_state_factory=Mock(return_value=state),
    )

    reflection_patch = {
        "reflection_queue": [],
        "pending_events": [
            {
                "event_id": "evt-1",
                "event_type": "actor_belief_updated",
                "actor_id": "analyst",
                "turn_index": 1,
                "visibility": "private",
                "payload": {"belief": "trust+"},
            }
        ],
    }
    event_patch = {
        "pending_events": [],
        "journal_events": ["🧠 [认知] analyst 的内部信念发生了变化。"],
    }

    with patch(
        "core.application.game_service.run_reflection_tick",
        new=AsyncMock(return_value=reflection_patch),
    ), patch(
        "core.application.game_service.event_drain_node",
        return_value=event_patch,
    ):
        asyncio.run(
            service.process_chat_turn(
                user_input="",
                intent="process_reflections",
                session_id="session-1",
            )
        )

    assert graph.ainvoke_calls == []
    assert len(graph.aupdate_calls) == 1
    persisted = graph.aupdate_calls[0]
    assert persisted["pending_events"] == []
    assert persisted["reflection_queue"] == []
    assert "journal_events" in persisted


def test_process_reflections_emits_processed_reflection_telemetry():
    state = {
        "entities": {"analyst": {"hp": 10, "faction": "party"}},
        "journal_events": [],
        "speaker_responses": [],
        "messages": [],
        "pending_events": [],
        "reflection_queue": [
            {
                "actor_id": "analyst",
                "reason": "process_private_memory",
                "priority": 2,
                "source_turn": 7,
                "payload": {},
            }
        ],
        "actor_runtime_state": {},
    }
    graph = _FakeGraph(state)
    service = GameService(
        saver_factory=Mock(return_value=_AsyncContextManager(object())),
        graph_builder=Mock(return_value=graph),
        initial_state_factory=Mock(return_value=state),
    )
    sink = InMemoryTelemetrySink()

    with telemetry_scope(sink):
        asyncio.run(
            service.process_chat_turn(
                user_input="",
                intent="process_reflections",
                session_id="session-reflect-processed",
            )
        )

    assert graph.ainvoke_calls == []
    assert graph.state["reflection_queue"] == []
    processed_events = [
        event
        for event in sink.events
        if event.get("event_name") == "reflection_processed"
        and event.get("payload", {}).get("status") == "processed"
    ]
    assert processed_events
    payload = processed_events[-1]["payload"]
    assert payload["actor_id"] == "analyst"
    assert payload["skip_reason"] == ""
    assert payload["emitted_event_count"] >= 1


def test_process_reflections_keeps_missing_runtime_request_and_emits_skipped_telemetry():
    state = {
        "entities": {"analyst": {"hp": 10, "faction": "party"}},
        "journal_events": [],
        "speaker_responses": [],
        "messages": [],
        "pending_events": [],
        "reflection_queue": [
            {
                "actor_id": "unknown_runtime_actor",
                "reason": "defer_until_runtime_ready",
                "priority": 2,
                "source_turn": 9,
                "payload": {},
            }
        ],
        "actor_runtime_state": {},
    }
    graph = _FakeGraph(state)
    service = GameService(
        saver_factory=Mock(return_value=_AsyncContextManager(object())),
        graph_builder=Mock(return_value=graph),
        initial_state_factory=Mock(return_value=state),
    )
    sink = InMemoryTelemetrySink()

    with telemetry_scope(sink):
        asyncio.run(
            service.process_chat_turn(
                user_input="",
                intent="process_reflections",
                session_id="session-reflect-skipped",
            )
        )

    assert graph.ainvoke_calls == []
    assert len(graph.state["reflection_queue"]) == 1
    assert graph.state["reflection_queue"][0]["actor_id"] == "unknown_runtime_actor"
    assert graph.state["pending_events"] == []
    skipped_events = [
        event
        for event in sink.events
        if event.get("event_name") == "reflection_processed"
        and event.get("payload", {}).get("status") == "skipped"
    ]
    assert skipped_events
    payload = skipped_events[-1]["payload"]
    assert payload["actor_id"] == "unknown_runtime_actor"
    assert payload["skip_reason"] == "runtime_missing"
