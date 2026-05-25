from __future__ import annotations

import json

import pytest

from core.eval.telemetry import (
    InMemoryTelemetrySink,
    JsonlTelemetrySink,
    emit_telemetry,
    telemetry_scope,
)


def test_jsonl_telemetry_sink_writes_jsonl_and_aggregates_summary(tmp_path):
    sink = JsonlTelemetrySink(telemetry_path=tmp_path / "telemetry.jsonl")
    sink.emit("turn_finished", {"duration_ms": 12})
    sink.emit("node_finished", {"node_name": "dm_analysis", "timing_ms": 5})
    sink.emit("memory_retrieval", {"hit_count": 2})
    sink.emit(
        "llm_call",
        {
            "token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )

    summary = sink.summary()
    assert summary["total_duration_ms"] == 12
    assert summary["node_durations_ms"]["dm_analysis"] == 5
    assert summary["retrieval_hit_count"] == 2
    assert summary["token_usage"]["total_tokens"] == 5

    lines = (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_name"] == "turn_finished"
    assert parsed[-1]["event_name"] == "llm_call"


def test_telemetry_scope_routes_emit_and_rejects_unknown_events():
    sink = InMemoryTelemetrySink()

    with telemetry_scope(sink):
        emit_telemetry("turn_started", session_id="s1")
        emit_telemetry("turn_finished", session_id="s1", duration_ms=7)
        with pytest.raises(ValueError):
            emit_telemetry("not_allowed_event", foo="bar")

    assert len(sink.events) == 2
    assert sink.events[0]["event_name"] == "turn_started"
    assert sink.summary()["total_duration_ms"] == 7
