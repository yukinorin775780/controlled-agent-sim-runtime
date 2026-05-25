from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

from core.actors.registry import get_default_actor_registry
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node


class _FakeRetriever:
    def retrieve_for_actor(self, *args, **kwargs):
        _ = (args, kwargs)
        return []

    def retrieve_for_director(self, *args, **kwargs):
        _ = (args, kwargs)
        return []


def _build_gatekeeper_talk_state(*, decoded: bool) -> dict:
    flags = {"world_hazard_lab_intro_entered": True}
    if decoded:
        flags.update(
            {
                "hazard_lab_diary_decoded": True,
                "hazard_lab_antidote_formula_fragment_known": True,
                "hazard_lab_key_hint_known": True,
            }
        )
    return {
        "current_speaker": "",
        "speaker_queue": [],
        "intent": "chat",
        "intent_context": {},
        "active_dialogue_target": "gatekeeper",
        "target": "",
        "user_input": "",
        "turn_count": 7,
        "current_location": "hazard_lab",
        "map_data": {"id": "hazard_lab"},
        "flags": flags,
        "entities": {
            "player": {
                "name": "玩家",
                "faction": "player",
                "status": "alive",
                "hp": 20,
                "max_hp": 20,
                "inventory": {},
            },
            "analyst": {
                "name": "Analyst",
                "faction": "party",
                "status": "alive",
                "hp": 11,
                "max_hp": 11,
                "inventory": {},
            },
            "scout": {
                "name": "Scout",
                "faction": "party",
                "status": "alive",
                "hp": 12,
                "max_hp": 12,
                "inventory": {},
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
                    "paranoia": {"current_value": 0},
                },
            },
        },
        "player_inventory": {},
        "pending_events": [],
        "speaker_responses": [],
        "messages": [],
        "journal_events": [],
        "actor_runtime_state": {},
    }


def _run_decoded_pressure_turn() -> dict:
    state = _build_gatekeeper_talk_state(decoded=True)
    state["user_input"] = "日记里写了你喝下危害狂暴灵药，钥匙和解药线索都和这件事有关。"

    dm_patch = asyncio.run(dm_node(state))
    patched_state = {**state, **dm_patch}

    fake_memory_service = Mock()
    fake_memory_service.retriever = _FakeRetriever()
    with patch(
        "core.actors.executor.get_default_memory_service",
        return_value=fake_memory_service,
    ):
        invocation_patch = asyncio.run(
            actor_invocation_node(
                patched_state,
                actor_registry=get_default_actor_registry(),
            )
        )
    drained_patch = event_drain_node({**patched_state, **invocation_patch})
    return {**patched_state, **invocation_patch, **drained_patch}


def test_dm_no_decoded_diary_does_not_route_truth_pressure_branch():
    state = _build_gatekeeper_talk_state(decoded=False)
    state["user_input"] = "我知道你喝了什么药，把钥匙给我。"

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
        dm_patch = asyncio.run(dm_node(state))

    assert dm_patch["intent_context"]["reason"] == "ordinary_gatekeeper_negotiation"
    assert dm_patch["intent_context"]["diary_negotiation_context"] == {}
    assert "hazard_lab_gatekeeper_truth_pressure" not in dm_patch.get("flags", {})


def test_decoded_diary_pressure_branch_sets_flag_and_changes_gatekeeper_state():
    result = _run_decoded_pressure_turn()

    assert result["actor_invocation_mode"] == "runtime"
    assert result["intent_context"]["reason"] == "diary_evidence_pressure"
    assert result["flags"]["hazard_lab_gatekeeper_truth_pressure"] is True
    gatekeeper = result["entities"]["gatekeeper"]
    assert gatekeeper["faction"] == "neutral"
    assert gatekeeper["dynamic_states"]["patience"]["current_value"] == 14
    assert gatekeeper["dynamic_states"]["fear"]["current_value"] == 6
    assert gatekeeper["dynamic_states"]["paranoia"]["current_value"] == 1
    assert result.get("combat_active") is not True


def test_decoded_diary_pressure_response_and_journal_are_visible():
    result = _run_decoded_pressure_turn()

    response_text = " ".join(text for _, text in result.get("speaker_responses", []))
    assert "危害" in response_text
    assert "污染" in response_text
    assert "危险" in response_text
    assert any("[交涉筹码]" in line for line in result.get("journal_events", []))


def test_diary_pressure_does_not_write_private_memory_for_uninvolved_actors():
    result = _run_decoded_pressure_turn()

    runtime_state = result.get("actor_runtime_state") or {}
    assert "scout" not in runtime_state
    assert "tactician" not in runtime_state
    assert "analyst" not in runtime_state
