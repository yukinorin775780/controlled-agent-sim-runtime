from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from core.eval.runner import _build_eval_llm_specs, run_eval_suite_sync
from core.eval.telemetry import emit_telemetry


def _write_case(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


class _FakeEvalGameService:
    process_calls: list[dict] = []
    snapshot_calls: list[dict] = []

    def __init__(self, db_path: str):
        _ = db_path
        self._flags = {}
        self._entities = {"analyst": {"hp": 10}}

    async def process_chat_turn(
        self,
        *,
        user_input: str = "",
        intent: str | None = None,
        session_id: str,
        character: str | None = None,
        map_id: str | None = None,
    ):
        self.process_calls.append(
            {
                "user_input": user_input,
                "intent": intent,
                "session_id": session_id,
                "character": character,
                "map_id": map_id,
            }
        )
        emit_telemetry("turn_finished", session_id=session_id, intent=intent or "", duration_ms=1)
        return {
            "responses": [],
            "journal_events": [],
            "current_location": "camp_center",
            "environment_objects": {},
            "party_status": {},
            "player_inventory": {},
            "combat_state": {},
        }

    async def get_state_snapshot(
        self,
        *,
        session_id: str,
        initialize_if_missing: bool = True,
        map_id: str | None = None,
    ):
        self.snapshot_calls.append(
            {
                "session_id": session_id,
                "initialize_if_missing": initialize_if_missing,
                "map_id": map_id,
            }
        )
        return {
            "game_state": {
                "flags": dict(self._flags),
                "entities": dict(self._entities),
            },
            "responses": [],
            "journal_events": [],
            "current_location": "camp_center",
            "environment_objects": {},
            "party_status": {},
            "player_inventory": {},
            "combat_state": {},
        }


def test_eval_runner_loads_yaml_and_writes_artifacts(tmp_path):
    _FakeEvalGameService.process_calls.clear()
    _FakeEvalGameService.snapshot_calls.clear()
    eval_dir = tmp_path / "evals" / "golden"
    output_root = tmp_path / "artifacts"
    _write_case(
        eval_dir / "runner_ok.yaml",
        {
            "session": {"id": "runner_ok"},
            "determinism": {"strict": False},
            "steps": [{"id": "s1", "intent": "init_sync", "user_input": ""}],
            "expected": {},
        },
    )

    with patch("core.eval.runner.GameService", new=_FakeEvalGameService):
        result = run_eval_suite_sync(
            suite="golden",
            eval_dir=eval_dir,
            case_selector=None,
            output_root=str(output_root),
        )

    assert result["ok"] is True
    assert result["case_count"] == 1

    case_summary = result["results"][0]
    artifacts = case_summary["artifacts"]
    assert Path(artifacts["run_dir"]).exists()
    assert Path(artifacts["transcript"]).exists()
    assert Path(artifacts["telemetry"]).exists()
    assert Path(artifacts["final_state"]).exists()
    assert Path(artifacts["summary"]).exists()

    transcript_lines = Path(artifacts["transcript"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(transcript_lines) == 1
    step_record = json.loads(transcript_lines[0])
    assert step_record["step_id"] == "s1"
    assert step_record["ok"] is True


def test_eval_runner_assertion_failure_contains_expected_actual_diff(tmp_path):
    _FakeEvalGameService.process_calls.clear()
    _FakeEvalGameService.snapshot_calls.clear()
    eval_dir = tmp_path / "evals" / "golden"
    output_root = tmp_path / "artifacts"
    _write_case(
        eval_dir / "runner_fail.yaml",
        {
            "session": {"id": "runner_fail"},
            "determinism": {"strict": False},
            "steps": [{"id": "s1", "intent": "init_sync", "user_input": ""}],
            "expected": {
                "state": {
                    "equals": {
                        "game_state.__definitely_missing__": 123,
                    }
                }
            },
        },
    )

    with patch("core.eval.runner.GameService", new=_FakeEvalGameService):
        result = run_eval_suite_sync(
            suite="golden",
            eval_dir=eval_dir,
            case_selector=None,
            output_root=str(output_root),
        )

    assert result["ok"] is False
    case_summary = result["results"][0]
    assert case_summary["ok"] is False
    assert case_summary["case_assertions"]["ok"] is False

    failure = case_summary["case_assertions"]["failures"][0]
    assert failure["path"] == "game_state.__definitely_missing__"
    assert failure["expected"] == 123
    assert failure["actual"] is None
    assert "does not exist" in failure["message"]


def test_eval_runner_passes_session_map_id_to_game_service(tmp_path):
    _FakeEvalGameService.process_calls.clear()
    _FakeEvalGameService.snapshot_calls.clear()
    eval_dir = tmp_path / "evals" / "golden"
    output_root = tmp_path / "artifacts"
    _write_case(
        eval_dir / "runner_map.yaml",
        {
            "session": {"id": "runner_map", "map_id": "hazard_lab"},
            "determinism": {"strict": False},
            "steps": [{"id": "s1", "intent": "init_sync", "user_input": ""}],
            "expected": {},
        },
    )

    with patch("core.eval.runner.GameService", new=_FakeEvalGameService):
        result = run_eval_suite_sync(
            suite="golden",
            eval_dir=eval_dir,
            case_selector=None,
            output_root=str(output_root),
        )

    assert result["ok"] is True
    assert _FakeEvalGameService.process_calls
    assert all(call["map_id"] == "hazard_lab" for call in _FakeEvalGameService.process_calls)
    assert _FakeEvalGameService.snapshot_calls
    assert all(call["map_id"] == "hazard_lab" for call in _FakeEvalGameService.snapshot_calls)


def test_eval_runner_passes_hazard_lab_map_id_into_init(tmp_path):
    _FakeEvalGameService.process_calls.clear()
    _FakeEvalGameService.snapshot_calls.clear()
    eval_dir = tmp_path / "evals" / "golden"
    output_root = tmp_path / "artifacts"
    _write_case(
        eval_dir / "runner_map_hazard.yaml",
        {
            "session": {"id": "runner_map_hazard", "map_id": "hazard_lab"},
            "determinism": {"strict": False},
            "steps": [{"id": "s1", "intent": "init_sync", "user_input": ""}],
            "expected": {},
        },
    )

    with patch("core.eval.runner.GameService", new=_FakeEvalGameService):
        result = run_eval_suite_sync(
            suite="golden",
            eval_dir=eval_dir,
            case_selector=None,
            output_root=str(output_root),
        )

    assert result["ok"] is True
    assert _FakeEvalGameService.process_calls
    assert _FakeEvalGameService.snapshot_calls
    assert all(call["map_id"] == "hazard_lab" for call in _FakeEvalGameService.process_calls)
    assert all(call["map_id"] == "hazard_lab" for call in _FakeEvalGameService.snapshot_calls)


def test_eval_runner_llm_specs_patch_dm_narration_generate_dialogue():
    specs = _build_eval_llm_specs()
    targets = {spec.target: spec.channel for spec in specs}
    assert targets["core.graph.nodes.dm.generate_dialogue"] == "generation"
