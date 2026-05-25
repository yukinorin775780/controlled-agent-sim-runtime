from __future__ import annotations

from core.eval.assertions import assert_eval_expectations


def test_eval_assertions_pass_for_matching_response_state_and_telemetry():
    expected = {
        "responses": {"contains": ["artifact"]},
        "telemetry": {
            "events_contains": [
                {"event_name": "actor_runtime_decision", "payload": {"actor_id": "analyst"}},
            ]
        },
        "state": {
            "equals": {"game_state.flags.world_artifact_revealed": True},
            "contains": {"game_state.party": ["analyst"]},
        },
        "visibility": {"required_paths": ["game_state.flags.world_artifact_revealed"]},
        "retrieval": {"max_hits": 2},
        "budget": {"max_total_tokens": 10, "max_latency_ms": 999},
    }
    response = {"responses": [{"speaker": "analyst", "text": "I know the artifact secret."}]}
    state = {
        "game_state": {
            "flags": {"world_artifact_revealed": True},
            "party": ["analyst", "scout"],
        }
    }
    telemetry_summary = {
        "retrieval_hit_count": 1,
        "total_duration_ms": 100,
        "token_usage": {"total_tokens": 4},
    }
    telemetry_events = [
        {
            "event_name": "actor_runtime_decision",
            "payload": {"actor_id": "analyst", "decision_kind": "speak"},
        }
    ]

    report = assert_eval_expectations(
        expected=expected,
        response=response,
        state=state,
        telemetry_summary=telemetry_summary,
        telemetry_events=telemetry_events,
    )

    assert report.ok is True
    assert report.failures == []


def test_eval_assertions_report_clear_diff_on_failures():
    expected = {
        "responses": {"not_contains": ["private_dagger"]},
        "telemetry": {"events_contains": [{"event_name": "reflection_processed"}]},
        "state": {"equals": {"game_state.flags.world_artifact_revealed": True}},
        "visibility": {"forbidden_paths": ["game_state.secret"]},
        "retrieval": {"min_hits": 3},
        "budget": {"max_total_tokens": 5},
    }
    response = {"responses": [{"speaker": "analyst", "text": "I saw private_dagger."}]}
    state = {"game_state": {"flags": {"world_artifact_revealed": False}, "secret": "leaked"}}
    telemetry_summary = {
        "retrieval_hit_count": 1,
        "token_usage": {"total_tokens": 12},
        "total_duration_ms": 0,
    }
    telemetry_events = []

    report = assert_eval_expectations(
        expected=expected,
        response=response,
        state=state,
        telemetry_summary=telemetry_summary,
        telemetry_events=telemetry_events,
    )

    assert report.ok is False
    failures = report.to_dict()["failures"]
    categories = {item["category"] for item in failures}
    assert "responses_not_contains" in categories
    assert "telemetry_events_contains" in categories
    assert "state_equals" in categories
    assert "visibility_forbidden" in categories
    assert "retrieval_min_hits" in categories
    assert "budget_tokens" in categories

    state_failure = next(item for item in failures if item["category"] == "state_equals")
    assert state_failure["path"] == "game_state.flags.world_artifact_revealed"
    assert state_failure["expected"] is True
    assert state_failure["actual"] is False
