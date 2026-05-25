import asyncio
from unittest.mock import AsyncMock, Mock

from core.actors.executor import process_reflection_queue
from core.actors.contracts import ReflectionRequest
from core.eval.telemetry import InMemoryTelemetrySink, telemetry_scope
from core.events.models import DomainEvent
from core.graph.nodes.event_drain import drain_reflection_queue


def test_drain_reflection_queue_processes_requests_in_priority_order():
    request_low = ReflectionRequest(
        actor_id="analyst",
        reason="after_small_talk",
        priority=1,
        source_turn=10,
        payload={},
    )
    request_high = ReflectionRequest(
        actor_id="analyst",
        reason="after_secret_reveal",
        priority=10,
        source_turn=11,
        payload={},
    )

    fake_runtime = AsyncMock()
    fake_runtime.reflect.side_effect = [
        (
            DomainEvent(
                "evt-high",
                "actor_belief_updated",
                "analyst",
                11,
                "private",
                {"belief": "trust+"},
            ),
        ),
        (
            DomainEvent(
                "evt-low",
                "actor_belief_updated",
                "analyst",
                10,
                "private",
                {"belief": "idle"},
            ),
        ),
    ]

    fake_registry = Mock()
    fake_registry.try_get.return_value = fake_runtime

    state = {
        "reflection_queue": [request_low, request_high],
        "pending_events": [],
    }

    result = asyncio.run(drain_reflection_queue(state, actor_registry=fake_registry))

    assert result["reflection_queue"] == []
    assert [evt["event_id"] for evt in result["pending_events"]] == ["evt-high", "evt-low"]


def test_process_reflection_queue_emits_processed_telemetry():
    request = ReflectionRequest(
        actor_id="analyst",
        reason="after_secret_reveal",
        priority=10,
        source_turn=11,
        payload={},
    )
    fake_runtime = AsyncMock()
    fake_runtime.reflect.return_value = (
        DomainEvent(
            "evt-a",
            "actor_belief_updated",
            "analyst",
            11,
            "private",
            {"belief": "trust+"},
        ),
        DomainEvent(
            "evt-b",
            "actor_memory_update_requested",
            "analyst",
            11,
            "private",
            {"text": "memory"},
        ),
    )
    fake_registry = Mock()
    fake_registry.try_get.return_value = fake_runtime
    sink = InMemoryTelemetrySink()

    with telemetry_scope(sink):
        result = asyncio.run(
            process_reflection_queue(
                state={"reflection_queue": [request], "pending_events": []},
                registry=fake_registry,
                max_items=1,
            )
        )

    assert result["reflection_queue"] == []
    assert len(result["pending_events"]) == 2

    processed_events = [
        event
        for event in sink.events
        if event.get("event_name") == "reflection_processed"
        and event.get("payload", {}).get("status") == "processed"
    ]
    assert processed_events
    payload = processed_events[-1]["payload"]
    assert payload["actor_id"] == "analyst"
    assert payload["reason"] == "after_secret_reveal"
    assert payload["skip_reason"] == ""
    assert payload["emitted_event_count"] == 2
    assert payload["queue_length_before"] == 1
    assert payload["queue_length_after"] == 0


def test_process_reflection_queue_keeps_request_when_runtime_missing_and_emits_skipped_telemetry():
    request = ReflectionRequest(
        actor_id="scout",
        reason="need_runtime",
        priority=5,
        source_turn=8,
        payload={"topic": "trust"},
    )
    fake_registry = Mock()
    fake_registry.try_get.return_value = None
    fake_registry.get.side_effect = KeyError("Unknown actor runtime")
    sink = InMemoryTelemetrySink()

    with telemetry_scope(sink):
        result = asyncio.run(
            process_reflection_queue(
                state={"reflection_queue": [request], "pending_events": []},
                registry=fake_registry,
                max_items=1,
            )
        )

    assert len(result["reflection_queue"]) == 1
    assert result["reflection_queue"][0]["actor_id"] == "scout"
    assert result["pending_events"] == []

    skipped_events = [
        event
        for event in sink.events
        if event.get("event_name") == "reflection_processed"
        and event.get("payload", {}).get("status") == "skipped"
    ]
    assert skipped_events
    payload = skipped_events[-1]["payload"]
    assert payload["actor_id"] == "scout"
    assert payload["reason"] == "need_runtime"
    assert payload["skip_reason"] == "runtime_missing"
    assert payload["emitted_event_count"] == 0
    assert payload["queue_length_before"] == 1
    assert payload["queue_length_after"] == 1
