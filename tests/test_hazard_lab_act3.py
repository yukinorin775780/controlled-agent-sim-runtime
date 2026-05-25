from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from core.actors.registry import get_default_actor_registry
from core.graph.nodes.dm import dm_node
from core.campaigns.hazard_lab import (
    ACT3_CHOICE_REBUKE_SCOUT,
    ACT3_CHOICE_SIDE_WITH_SCOUT,
    detect_lab_act3_choice,
)
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.input import input_node
from core.graph.nodes.lore import lore_node
from core.systems import mechanics


def _build_act3_state(user_input: str) -> dict:
    return {
        "current_speaker": "scout",
        "speaker_queue": ["analyst"],
        "intent": "CHAT",
        "intent_context": {
            "action_actor": "player",
            "action_target": "gatekeeper",
        },
        "active_dialogue_target": "gatekeeper",
        "user_input": user_input,
        "turn_count": 42,
        "current_location": "hazard_lab",
        "map_data": {"id": "hazard_lab"},
        "flags": {},
        "entities": {
            "player": {
                "name": "玩家",
                "faction": "player",
                "status": "alive",
                "hp": 20,
                "max_hp": 20,
                "inventory": {},
                "x": 2,
                "y": 2,
            },
            "scout": {
                "name": "Scout",
                "faction": "party",
                "status": "alive",
                "hp": 12,
                "max_hp": 12,
                "affection": 0,
                "inventory": {"dagger": 1},
                "dynamic_states": {
                    "affection": {"current_value": 0},
                },
            },
            "analyst": {
                "name": "Analyst",
                "faction": "party",
                "status": "alive",
                "hp": 11,
                "max_hp": 11,
                "inventory": {"private_relic": 1},
            },
            "tactician": {
                "name": "Tactician",
                "faction": "party",
                "status": "alive",
                "hp": 13,
                "max_hp": 13,
                "inventory": {},
            },
            "gatekeeper": {
                "name": "Gatekeeper",
                "faction": "neutral",
                "status": "alive",
                "hp": 18,
                "max_hp": 18,
                "inventory": {"heavy_iron_key": 1},
                "dynamic_states": {
                    "patience": {"current_value": 15},
                    "fear": {"current_value": 5},
                },
            },
        },
        "pending_events": [],
        "speaker_responses": [],
        "messages": [],
        "flags": {"world_hazard_lab_intro_entered": True},
    }


def _build_secret_study_base_state(user_input: str = "") -> dict:
    state = _build_act3_state(user_input)
    state["current_speaker"] = ""
    state["speaker_queue"] = []
    state["active_dialogue_target"] = None
    state["intent_context"] = {"action_actor": "player"}
    state["flags"] = {
        "world_hazard_lab_intro_entered": True,
        "act2_corridor_exit_lockpick_success": False,
        "act2_secret_study_hint_given": True,
        "act2_secret_study_route_unlocked": True,
    }
    state["environment_objects"] = {
        "hazard_diary": {
            "id": "hazard_diary",
            "type": "readable",
            "name": "沾满血污的日记本",
            "lore_id": "hazard_diary_1",
            "x": 15,
            "y": 3,
        },
        "chemical_notes": {
            "id": "chemical_notes",
            "type": "readable",
            "name": "化学残页",
            "lore_id": "hazard_chemical_notes",
            "x": 15,
            "y": 4,
        },
        "iron_key_sketch": {
            "id": "iron_key_sketch",
            "type": "readable",
            "name": "重铁钥匙草图",
            "lore_id": "hazard_iron_key_sketch",
            "x": 16,
            "y": 3,
        },
    }
    state["entities"]["player"]["ability_scores"] = {"INT": 16}
    return state


def test_detect_lab_act3_choice_parser_handles_side_and_rebuke():
    side_state = {
        "map_data": {"id": "hazard_lab"},
        "active_dialogue_target": "gatekeeper",
        "user_input": "侦察员说得对，我们一起嘲笑这个自大的训练无人机。",
    }
    rebuke_state = {
        "map_data": {"id": "hazard_lab"},
        "active_dialogue_target": "gatekeeper",
        "user_input": "侦察员，闭嘴，别再拱火了。",
    }
    key_request_state = {
        "map_data": {"id": "hazard_lab"},
        "active_dialogue_target": "gatekeeper",
        "user_input": "把钥匙给我。",
    }

    assert detect_lab_act3_choice(side_state) == ACT3_CHOICE_SIDE_WITH_SCOUT
    assert detect_lab_act3_choice(rebuke_state) == ACT3_CHOICE_REBUKE_SCOUT
    assert detect_lab_act3_choice(key_request_state) == ""


def test_act3_side_with_scout_updates_state_via_event_drain():
    state = _build_act3_state("侦察员说得对，我们一起嘲笑 Gatekeeper。")

    class _FakeRetriever:
        def retrieve_for_actor(self, query):
            _ = query
            return []

        def retrieve_for_director(self, query):
            _ = query
            return []

    fake_memory_service = Mock()
    fake_memory_service.retriever = _FakeRetriever()
    with patch(
        "core.actors.executor.get_default_memory_service",
        return_value=fake_memory_service,
    ):
        invocation_patch = asyncio.run(
            actor_invocation_node(
                state,
                actor_registry=get_default_actor_registry(),
            )
        )

    assert invocation_patch["actor_invocation_mode"] == "runtime"
    assert invocation_patch["actor_invocation_reason"] == "party_turn_runtime_multi"
    assert invocation_patch["speaker_queue"] == []

    patched_state = {**state, **invocation_patch}
    drain_patch = event_drain_node(patched_state)

    assert drain_patch["entities"]["scout"]["affection"] == 2
    assert any(
        "玩家与我一起嘲笑了 Gatekeeper" in item
        for item in drain_patch["actor_runtime_state"]["scout"]["memory_notes"]
    )
    gatekeeper = drain_patch["entities"]["gatekeeper"]
    assert gatekeeper["faction"] == "hostile"
    assert gatekeeper["dynamic_states"]["patience"]["current_value"] == 0
    assert drain_patch["combat_phase"] == "IN_COMBAT"
    assert drain_patch["combat_active"] is True
    assert "gatekeeper" in drain_patch["initiative_order"]
    assert drain_patch["flags"]["hazard_lab_player_sided_with_scout"] is True
    assert "analyst" not in drain_patch["actor_runtime_state"]
    assert "tactician" not in drain_patch["actor_runtime_state"]


def test_act3_rebuke_scout_still_triggers_combat_due_to_paranoia():
    state = _build_act3_state("侦察员，闭嘴。别再嘲笑他了。")

    class _FakeRetriever:
        def retrieve_for_actor(self, query):
            _ = query
            return []

        def retrieve_for_director(self, query):
            _ = query
            return []

    fake_memory_service = Mock()
    fake_memory_service.retriever = _FakeRetriever()

    with patch(
        "core.actors.executor.get_default_memory_service",
        return_value=fake_memory_service,
    ):
        invocation_patch = asyncio.run(
            actor_invocation_node(
                state,
                actor_registry=get_default_actor_registry(),
            )
        )

    patched_state = {**state, **invocation_patch}
    drain_patch = event_drain_node(patched_state)

    assert drain_patch["entities"]["scout"]["affection"] == -3
    assert any(
        "玩家当众训斥了我" in item
        for item in drain_patch["actor_runtime_state"]["scout"]["memory_notes"]
    )
    assert drain_patch["flags"]["hazard_lab_player_sided_with_scout"] is False
    assert drain_patch["flags"]["hazard_lab_gatekeeper_combat_triggered"] is True
    gatekeeper = drain_patch["entities"]["gatekeeper"]
    assert gatekeeper["faction"] == "hostile"
    assert gatekeeper["dynamic_states"]["patience"]["current_value"] == 0
    assert gatekeeper["dynamic_states"]["paranoia"]["current_value"] >= 1
    assert drain_patch["combat_active"] is True
    assert any("paranoia" in event for event in drain_patch.get("journal_events", []))


def test_act3_secret_study_entry_sets_flags_without_reading_diary():
    state = _build_secret_study_base_state("调查墙壁，寻找暗门。")
    state["intent"] = "INTERACT"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "cracked_wall",
    }

    result = mechanics.execute_interact_action(state)

    assert result["flags"]["act3_secret_study_entered"] is True
    assert result["flags"]["act3_secret_study_discovered"] is True
    assert result["flags"]["act3_cracked_wall_found"] is True
    assert "act3_diary_read" not in result["flags"]
    assert any("[秘密书房]" in line for line in result["journal_events"])


def test_act3_secret_study_companion_observations_use_actor_runtime():
    state = _build_secret_study_base_state("队友们看看这间书房。")
    state["flags"]["act3_secret_study_entered"] = True
    state["current_speaker"] = "scout"
    state["speaker_queue"] = ["analyst", "tactician"]
    state["intent"] = "CHAT"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "room_c_secret_study",
        "secret_study_observation_context": {
            "topic": "secret_study_observation",
            "location_id": "room_c_secret_study",
            "observations": {
                "scout": "practical_clues",
                "analyst": "necromancy_pollution",
                "tactician": "tactical_impatience",
            },
        },
    }

    class _FakeRetriever:
        def retrieve_for_actor(self, *args, **kwargs):
            _ = (args, kwargs)
            return []

        def retrieve_for_director(self, *args, **kwargs):
            _ = (args, kwargs)
            return []

    fake_memory_service = Mock()
    fake_memory_service.retriever = _FakeRetriever()
    with patch("core.actors.executor.get_default_memory_service", return_value=fake_memory_service):
        invocation_patch = asyncio.run(
            actor_invocation_node(state, actor_registry=get_default_actor_registry())
        )

    drained = event_drain_node({**state, **invocation_patch})
    journal = "\n".join(drained.get("journal_events", []))

    assert "[书房观察] scout -> practical_clues" in journal
    assert "[书房观察] analyst -> necromancy_pollution" in journal
    assert "[书房观察] tactician -> tactical_impatience" in journal
    assert drained.get("player_inventory", {}) == state.get("player_inventory", {})
    assert drained.get("combat_active") is not True


def test_act3_diary_success_information_advantage_then_gatekeeper_pressure(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("用奥术知识阅读 hazard_diary。")
    state["flags"]["act3_secret_study_entered"] = True
    state["intent"] = "READ"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "hazard_diary",
        "skill": "arcana",
    }

    lore_patch = lore_node(state)
    drained = event_drain_node({**state, **lore_patch})
    result = {
        **state,
        **lore_patch,
        **drained,
        "journal_events": list(lore_patch.get("journal_events", [])) + list(drained.get("journal_events", [])),
    }

    assert result["flags"]["act3_diary_read"] is True
    assert result["flags"]["act3_diary_decoded"] is True
    assert result["flags"]["act3_gatekeeper_potion_truth_known"] is True
    assert result["flags"]["act3_heavy_key_hint_known"] is True
    assert result["flags"]["act3_party_knows_gatekeeper_truth"] is True
    assert any("Gatekeeper" in note and "heavy_iron_key" in note for note in result["actor_runtime_state"]["player"]["memory_notes"])
    assert any(
        "Gatekeeper" in note and "毒气" in note and "heavy_iron_key" in note
        for note in result["actor_runtime_state"]["__party_shared__"]["memory_notes"]
    )
    assert any("[目标更新]" in line for line in result["journal_events"])

    pressure_state = {
        **result,
        "intent": "chat",
        "active_dialogue_target": "gatekeeper",
        "target": "",
        "user_input": "日记里写了你喝下危害狂暴灵药，钥匙和毒气真相都和你有关。",
        "current_speaker": "",
        "speaker_queue": [],
    }
    pressure_patch = asyncio.run(dm_node(pressure_state))
    assert pressure_patch["intent_context"]["reason"] == "diary_evidence_pressure"


def test_act3_diary_failure_fragment_only_blocks_gatekeeper_pressure(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("阅读 hazard_diary。")
    state["flags"]["act3_secret_study_entered"] = True
    state["entities"]["player"]["ability_scores"] = {"INT": 8}
    state["intent"] = "READ"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "hazard_diary",
    }

    lore_patch = lore_node(state)
    drained = event_drain_node({**state, **lore_patch})
    result = {
        **state,
        **lore_patch,
        **drained,
        "journal_events": list(lore_patch.get("journal_events", [])) + list(drained.get("journal_events", [])),
    }

    assert result["flags"]["act3_diary_read"] is True
    assert result["flags"]["act3_diary_decoded"] is False
    assert result["flags"]["act3_gatekeeper_potion_truth_known"] is False
    assert "act3_party_knows_gatekeeper_truth" not in result["flags"]
    assert "__party_shared__" not in result.get("actor_runtime_state", {})
    assert any("碎片" in note or "训练无人机、箱子、毒气" in note for note in result["actor_runtime_state"]["player"]["memory_notes"])

    pressure_state = {
        **result,
        "intent": "chat",
        "active_dialogue_target": "gatekeeper",
        "target": "",
        "user_input": "日记里写了你喝下危害狂暴灵药，把钥匙给我。",
        "current_speaker": "",
        "speaker_queue": [],
    }
    with patch(
        "core.graph.nodes.dm.analyze_intent",
        return_value={
            "action_type": "DIALOGUE_REPLY",
            "difficulty_class": 12,
            "reason": "ordinary_gatekeeper_negotiation",
            "is_probing_secret": False,
            "responders": ["gatekeeper"],
            "affection_changes": {},
            "flags_changed": {},
            "item_transfers": [],
            "hp_changes": [],
            "action_actor": "player",
            "action_target": "gatekeeper",
        },
    ):
        pressure_patch = asyncio.run(dm_node(pressure_state))
    assert pressure_patch["intent_context"]["reason"] != "diary_evidence_pressure"


def test_act3_chemical_notes_grant_diary_context_bonus(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("阅读 chemical_notes。")
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "chemical_notes"}

    result = lore_node(state)

    assert result["flags"]["act3_chemical_notes_seen"] is True
    assert result["flags"]["act3_diary_context_gathered"] is True
    assert result["flags"]["act3_diary_context_bonus"] == 10
    assert any("[线索整合] chemical_notes -> diary_context" in line for line in result["journal_events"])
    assert any("术语变得可读" in line for line in result["journal_events"])


def test_act3_input_routes_chemical_notes_read():
    state = _build_secret_study_base_state("阅读 chemical_notes")

    result = input_node(state)

    assert result["intent"] == "READ"
    assert result["target"] == "chemical_notes"
    assert result["source"] == "act3_study_context"
    assert result["intent_context"]["action_target"] == "chemical_notes"


def test_act3_input_routes_chemical_notes_chinese_alias():
    state = _build_secret_study_base_state("阅读药剂笔记")

    result = input_node(state)

    assert result["intent"] == "READ"
    assert result["target"] == "chemical_notes"


def test_act3_input_routes_iron_key_sketch_chinese_alias():
    state = _build_secret_study_base_state("查看铁钥匙草图")

    result = input_node(state)

    assert result["intent"] == "READ"
    assert result["target"] == "iron_key_sketch"


def test_act3_chemical_notes_alias_grants_diary_context_bonus(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("阅读药剂笔记")
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "药剂笔记"}

    result = lore_node(state)

    assert result["flags"]["act3_chemical_notes_seen"] is True
    assert result["flags"]["act3_diary_context_gathered"] is True
    assert result["flags"]["act3_diary_context_bonus"] == 10


def test_act3_iron_key_sketch_alias_grants_diary_context_bonus(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("查看铁钥匙草图")
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "铁钥匙草图"}

    result = lore_node(state)

    assert result["flags"]["act3_key_sketch_seen"] is True
    assert result["flags"]["act3_diary_context_gathered"] is True
    assert result["flags"]["act3_diary_context_bonus"] == 10


def test_act3_diary_succeeds_after_context_gathered(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    monkeypatch.setattr("core.graph.nodes.lore.dice_module.random.randint", lambda start, end: 1)
    state = _build_secret_study_base_state("阅读 hazard_diary。")
    state["flags"]["act3_secret_study_entered"] = True
    state["flags"]["act3_diary_context_gathered"] = True
    state["flags"]["act3_diary_context_bonus"] = 10
    state["entities"]["player"]["ability_scores"] = {"INT": 8}
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "hazard_diary"}

    lore_patch = lore_node(state)
    drained = event_drain_node({**state, **lore_patch})
    result = {**state, **lore_patch, **drained}

    assert result["flags"]["act3_diary_read"] is True
    assert result["flags"]["act3_diary_decoded"] is True
    assert result["flags"]["act3_gatekeeper_potion_truth_known"] is True
    assert result["flags"]["act3_heavy_key_hint_known"] is True
    assert result["latest_roll"]["result"]["result_type"] == "CONTEXT_SUCCESS"
    assert result["latest_roll"]["result"]["context_bonus"] == 10
    assert "__party_shared__" in result["actor_runtime_state"]


def test_act3_diary_can_still_fail_without_context(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    monkeypatch.setattr("core.graph.nodes.lore.dice_module.random.randint", lambda start, end: 1)
    state = _build_secret_study_base_state("阅读 hazard_diary。")
    state["flags"]["act3_secret_study_entered"] = True
    state["entities"]["player"]["ability_scores"] = {"INT": 10}
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "hazard_diary"}

    lore_patch = lore_node(state)

    assert lore_patch["flags"]["act3_diary_read"] is True
    assert lore_patch["flags"]["act3_diary_decoded"] is False
    assert lore_patch["flags"]["act3_gatekeeper_potion_truth_known"] is False
    assert lore_patch["latest_roll"]["result"]["result_type"] == "CRITICAL_FAILURE"


def test_full_path_uses_study_context_before_diary_truth():
    golden_path = Path("evals/golden/hazard_lab_full_path_act2_to_act4_truth_negotiation.yaml")
    payload = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    steps = payload["steps"]
    step_ids = [step["id"] for step in steps]

    assert step_ids.index("study_context_before_diary") < step_ids.index("read_diary_success")
    context_step = next(step for step in steps if step["id"] == "study_context_before_diary")
    assert context_step["expected"]["state"]["equals"]["game_state.flags.act3_diary_context_gathered"] is True
    assert context_step["expected"]["state"]["equals"]["game_state.flags.act3_chemical_notes_seen"] is True


def test_act3_chemical_notes_and_key_sketch_flags(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_secret_study_base_state("阅读 chemical_notes")
    state["intent"] = "READ"
    state["intent_context"] = {"action_actor": "player", "action_target": "chemical_notes"}
    chemical = lore_node(state)
    assert chemical["flags"]["act3_chemical_notes_seen"] is True
    assert any("analyst -> necromancy_pollution" in line for line in chemical["journal_events"])

    sketch_state = {**state, **chemical, "user_input": "阅读 iron_key_sketch"}
    sketch_state["intent_context"] = {"action_actor": "player", "action_target": "iron_key_sketch"}
    sketch = lore_node(sketch_state)
    assert sketch["flags"]["act3_heavy_key_hint_known"] is True
    assert any("scout -> practical_clues" in line for line in sketch["journal_events"])
