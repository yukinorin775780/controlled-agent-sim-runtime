from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from core.eval.init import discover_golden_eval_cases
from core.eval.runner import run_eval_suite_sync
from core.eval.telemetry import emit_telemetry


class _FakeEvalGameService:
    def __init__(self, db_path: str):
        _ = db_path

    async def process_chat_turn(
        self,
        *,
        user_input: str = "",
        intent: str | None = None,
        session_id: str,
        character: str | None = None,
        map_id: str | None = None,
    ):
        _ = (user_input, intent, session_id, character, map_id)
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
        _ = (session_id, initialize_if_missing, map_id)
        return {
            "game_state": {
                "flags": {},
                "entities": {"analyst": {"hp": 10}},
            },
            "responses": [],
            "journal_events": [],
            "current_location": "camp_center",
            "environment_objects": {},
            "party_status": {},
            "player_inventory": {},
            "combat_state": {},
        }


def test_golden_suite_contains_expected_minimum_cases():
    cases = discover_golden_eval_cases("evals/golden")
    case_ids = {case.session_id for case in cases}
    assert {
        "scout_runtime_isolation",
        "scout_rejects_unwanted_gift",
        "background_reflection_after_conflict",
        "combat_damage_then_healing",
        "tactician_runtime_registry",
        "player_gives_potion_to_analyst",
        "analyst_accepts_healing_potion",
        "analyst_artifact_probe",
        "analyst_artifact_secret_actor_visibility",
        "analyst_secret_not_visible_to_scout",
        "gift_potion_acceptance",
        "combat_opening_round",
        "party_banter_after_player_choice",
        "tactician_disagrees_with_mercy_choice",
        "hazard_lab_gatekeeper_key_path",
        "hazard_lab_gatekeeper_mercy_execute",
        "hazard_lab_gatekeeper_mercy_spare",
        "hazard_lab_act1_trap_perception",
        "hazard_lab_act2_diary_int_success",
        "hazard_lab_act2_diary_int_failure",
        "hazard_lab_act2_scout_reveals_gas_trap",
        "hazard_lab_act2_disarm_trap_success",
        "hazard_lab_act2_disarm_trap_failure_triggers_poison",
        "hazard_lab_act2_scout_ordered_to_disarm",
        "hazard_lab_act2_corridor_door_lockpick_success_skips_study",
        "hazard_lab_act2_corridor_door_lockpick_failure_hints_secret_study",
        "hazard_lab_act3_side_with_scout",
        "hazard_lab_act3_rebuke_scout",
        "hazard_lab_act3_secret_study_entry",
        "hazard_lab_act3_study_companion_observations",
        "hazard_lab_act3_diary_success_information_advantage",
        "hazard_lab_act3_diary_failure_fragment_only",
        "hazard_lab_act3_chemical_notes_and_key_sketch",
        "hazard_lab_act4_loot_key_and_escape",
        "hazard_lab_full_path_act2_to_act4_truth_negotiation",
        "hazard_lab_key_aware_guidance",
        "hazard_lab_diary_changes_negotiation",
        "hazard_lab_scout_trap_intervention",
        "hazard_lab_scout_remembers_rebuke",
        "reflection_queue_drain",
        "world_flag_reveal",
        "world_flag_reveal_to_visible_party",
    }.issubset(case_ids)


def test_golden_suite_smoke_passes(tmp_path):
    eval_dir = tmp_path / "evals" / "golden"
    eval_dir.mkdir(parents=True, exist_ok=True)
    case_payload = {
        "session": {"id": "smoke_case"},
        "determinism": {"strict": False},
        "steps": [{"id": "s1", "intent": "init_sync", "user_input": ""}],
        "expected": {},
    }
    (eval_dir / "smoke_case.yaml").write_text(
        yaml.safe_dump(case_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with patch("core.eval.runner.GameService", new=_FakeEvalGameService):
        result = run_eval_suite_sync(
            suite="golden",
            eval_dir=Path(eval_dir),
            case_selector=None,
            output_root=str(tmp_path / "eval_artifacts"),
        )

    assert result["case_count"] == 1
    assert result["ok"] is True
    assert result["failed_count"] == 0
