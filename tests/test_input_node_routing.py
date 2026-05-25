import asyncio
from unittest.mock import patch

from core.graph.nodes.dm import dm_node
from core.graph.nodes.input import input_node


def _base_state():
    return {
        "entities": {},
        "player_inventory": {},
        "journal_events": [],
        "map_data": {"id": "hazard_lab"},
    }


def test_input_node_demotes_stale_read_unknown_to_chat_gatekeeper_for_act3_choice_text():
    state = {
        **_base_state(),
        "user_input": "侦察员说得对，我们一起嘲笑 Gatekeeper。",
        "intent": "READ",
        "target": "unknown",
        "source": "",
    }

    patch = input_node(state)

    assert patch["intent"] == "CHAT"
    assert patch["target"] == "gatekeeper"
    assert patch["intent_context"]["action_target"] == "gatekeeper"


def test_input_node_demotes_stale_read_unknown_to_pending_for_plain_text():
    state = {
        **_base_state(),
        "user_input": "我观察四周有没有陷阱。",
        "intent": "READ",
        "target": "unknown",
        "source": "",
    }

    patch = input_node(state)

    assert patch["intent"] == "pending"
    assert patch["target"] == "unknown"


def test_input_node_keeps_read_with_explicit_target():
    state = {
        **_base_state(),
        "user_input": "阅读 日记",
        "intent": "READ",
        "target": "hazard_diary",
        "source": "interaction",
    }

    patch = input_node(state)

    assert patch["intent"] == "READ"
    assert patch["target"] == "hazard_diary"


def test_input_node_routes_hazard_diary_text_to_read_target():
    state = {
        **_base_state(),
        "user_input": "读日记",
        "intent": "chat",
        "target": "",
        "source": "text_input",
    }

    patch = input_node(state)

    assert patch["intent"] == "READ"
    assert patch["target"] == "hazard_diary"
    assert patch["source"] == "ui_text_normalized"
    assert patch["intent_context"]["action_target"] == "hazard_diary"


def test_input_node_routes_scout_trap_disarm_to_disarm():
    state = {
        **_base_state(),
        "user_input": "侦察员，解除毒气陷阱。",
        "intent": "chat",
        "target": "",
        "source": "text_input",
    }

    patch = input_node(state)

    assert patch["intent"] == "DISARM"
    assert patch["target"] == "gas_trap_1"
    assert patch["source"] == "ui_text_normalized"
    assert patch["intent_context"]["action_actor"] == "scout"
    assert patch["intent_context"]["action_target"] == "gas_trap_1"
    assert patch["intent_context"]["action"] == "disarm_trap"


def test_input_node_overrides_gatekeeper_diary_truth_use_item_to_chat():
    state = {
        **_base_state(),
        "user_input": "Gatekeeper，我读了日记，知道你喝了危害药剂，也知道钥匙和实验的真相。",
        "intent": "USE_ITEM",
        "target": "gatekeeper",
        "source": "text_input",
    }

    patch = input_node(state)

    assert patch["intent"] == "CHAT"
    assert patch["target"] == "gatekeeper"
    assert patch["source"] == "ui_text_normalized"
    assert patch["intent_context"]["action_target"] == "gatekeeper"
    assert patch["intent_context"]["diary_negotiation_hint"] is True


def test_input_node_keeps_explicit_attack_gatekeeper_as_attack():
    state = {
        **_base_state(),
        "user_input": "attack gatekeeper",
        "intent": "ATTACK",
        "target": "gatekeeper",
        "source": "text_input",
    }

    patch = input_node(state)

    assert patch["intent"] == "ATTACK"
    assert patch["target"] == "gatekeeper"


def test_use_healing_potion_caps_target_at_max_hp():
    state = {
        **_base_state(),
        "user_input": "/use healing_potion analyst",
        "intent": "chat",
        "player_inventory": {"healing_potion": 2},
        "entities": {
            "analyst": {
                "name": "Analyst",
                "hp": 15,
                "max_hp": 18,
                "active_buffs": [],
                "inventory": {},
            }
        },
    }

    patch = input_node(state)

    assert patch["entities"]["analyst"]["hp"] == 18
    assert patch["entities"]["analyst"]["max_hp"] == 18
    assert patch["player_inventory"] == {"healing_potion": 1}


def test_dm_hazard_diary_negotiation_from_ui_text_sets_target_gatekeeper():
    state = {
        **_base_state(),
        "user_input": "Gatekeeper，我读了日记，知道你喝了危害药剂，也知道钥匙和实验的真相。",
        "intent": "USE_ITEM",
        "target": "",
        "source": "text_input",
        "active_dialogue_target": "",
        "flags": {
            "hazard_lab_diary_decoded": True,
            "hazard_lab_antidote_formula_fragment_known": True,
            "hazard_lab_key_hint_known": True,
        },
        "entities": {
            "player": {"name": "玩家", "faction": "player", "status": "alive", "hp": 20, "inventory": {}},
            "gatekeeper": {
                "name": "Gatekeeper",
                "faction": "neutral",
                "status": "alive",
                "hp": 18,
                "inventory": {"heavy_iron_key": 1},
                "dynamic_states": {
                    "patience": {"current_value": 15},
                    "fear": {"current_value": 5},
                    "paranoia": {"current_value": 0},
                },
            },
            "analyst": {"name": "Analyst", "faction": "party", "status": "alive", "hp": 11, "inventory": {}},
        },
    }

    with patch("core.graph.nodes.dm.analyze_intent") as analyze:
        dm_patch = asyncio.run(dm_node(state))

    analyze.assert_not_called()
    assert dm_patch["intent"] == "CHAT"
    assert dm_patch["intent_context"]["reason"] == "diary_evidence_pressure"
    assert dm_patch["intent_context"]["action_target"] == "gatekeeper"
    assert dm_patch["intent_context"]["diary_negotiation_context"]["target_actor_id"] == "gatekeeper"


def test_input_node_reset_keeps_current_map_id(monkeypatch):
    state = {
        **_base_state(),
        "user_input": "/reset",
        "messages": [],
        "map_data": {"id": "hazard_lab"},
    }
    observed = {}

    def _fake_initial_world_state(map_id="training_range"):
        observed["map_id"] = map_id
        return {
            "entities": {},
            "map_data": {"id": map_id},
            "player_inventory": {},
            "turn_count": 0,
            "combat_phase": "OUT_OF_COMBAT",
            "combat_active": False,
            "initiative_order": [],
            "current_turn_index": 0,
            "turn_resources": {},
            "recent_barks": [],
            "active_dialogue_target": None,
            "demo_cleared": False,
            "time_of_day": "晨曦 (Morning)",
            "flags": {},
            "messages": [],
            "journal_events": [],
            "current_location": "危害研究员的废弃实验室",
            "environment_objects": {},
        }

    monkeypatch.setattr(
        "core.systems.world_init.get_initial_world_state",
        _fake_initial_world_state,
    )
    patch = input_node(state)
    assert observed["map_id"] == "hazard_lab"
    assert patch["map_data"]["id"] == "hazard_lab"
