import asyncio
from unittest.mock import patch

from core.graph.nodes.dm import dm_node


def test_dm_node_prefers_interact_for_hazard_door_target():
    state = {
        "intent": "CHAT",
        "user_input": "打开门",
        "target": "heavy_oak_door_1",
        "source": "text_input",
        "active_dialogue_target": "gatekeeper",
        "time_of_day": "晨曦 (Morning)",
        "map_data": {"id": "hazard_lab"},
        "flags": {},
        "entities": {
            "player": {"name": "玩家", "faction": "player", "status": "alive", "hp": 20},
            "heavy_oak_door_1": {"name": "门", "entity_type": "door", "status": "closed"},
            "gatekeeper": {"name": "Gatekeeper", "faction": "neutral", "status": "alive", "hp": 18},
        },
        "environment_objects": {},
        "intent_context": {"action_target": "heavy_oak_door_1", "source": "text_input"},
    }

    with patch("core.graph.nodes.dm.analyze_intent", side_effect=AssertionError("should not call llm")):
        result = asyncio.run(dm_node(state))

    assert result["intent"] == "INTERACT"
    assert result["current_speaker"] != "heavy_oak_door_1"
    assert result["intent_context"]["action_target"] == "heavy_oak_door_1"


def test_dm_node_backfills_hazard_door_target_when_missing_for_open_door_text():
    state = {
        "intent": "INTERACT",
        "user_input": "打开门",
        "target": "",
        "source": "text_input",
        "active_dialogue_target": "gatekeeper",
        "time_of_day": "晨曦 (Morning)",
        "map_data": {"id": "hazard_lab"},
        "flags": {},
        "entities": {
            "player": {"name": "玩家", "faction": "player", "status": "alive", "hp": 20},
            "gatekeeper": {"name": "Gatekeeper", "faction": "neutral", "status": "alive", "hp": 18},
        },
        "environment_objects": {
            "heavy_oak_door_1": {"id": "heavy_oak_door_1", "type": "door", "status": "closed"},
        },
        "intent_context": {"action_target": "", "source": "text_input"},
    }

    with patch("core.graph.nodes.dm.analyze_intent", side_effect=AssertionError("should not call llm")):
        result = asyncio.run(dm_node(state))

    assert result["intent"] == "INTERACT"
    assert result["intent_context"]["action_target"] == "heavy_oak_door_1"


def test_dm_node_keeps_explicit_attack_door_as_attack():
    state = {
        "intent": "pending",
        "user_input": "攻击门",
        "target": "",
        "source": "text_input",
        "active_dialogue_target": "gatekeeper",
        "time_of_day": "晨曦 (Morning)",
        "map_data": {"id": "hazard_lab"},
        "flags": {},
        "entities": {
            "player": {"name": "玩家", "faction": "player", "status": "alive", "hp": 20},
            "gatekeeper": {"name": "Gatekeeper", "faction": "neutral", "status": "alive", "hp": 18},
        },
        "environment_objects": {
            "heavy_oak_door_1": {"id": "heavy_oak_door_1", "type": "door", "status": "closed"},
        },
        "intent_context": {"action_target": "", "source": "text_input"},
    }

    with patch("core.graph.nodes.dm.analyze_intent", return_value={
        "action_type": "ATTACK",
        "difficulty_class": 12,
        "reason": "explicit_attack",
        "is_probing_secret": False,
        "responders": ["gatekeeper"],
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": "player",
        "action_target": "heavy_oak_door_1",
    }):
        result = asyncio.run(dm_node(state))

    assert result["intent"] == "ATTACK"
    assert result["intent_context"]["action_target"] == "heavy_oak_door_1"
