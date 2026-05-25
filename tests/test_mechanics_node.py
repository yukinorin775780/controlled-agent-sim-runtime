from core.graph.nodes.mechanics import mechanics_node
from core.systems.world_init import get_initial_world_state


def test_mechanics_node_propagates_demo_cleared_from_interact_result():
    state = get_initial_world_state(map_id="hazard_lab")
    state["intent"] = "INTERACT"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "heavy_oak_door_1",
    }
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4
    state["player_inventory"]["heavy_iron_key"] = 1

    result = mechanics_node(state)

    assert result["entities"]["heavy_oak_door_1"]["is_open"] is True
    assert result["demo_cleared"] is True


def test_mechanics_node_interact_without_target_returns_explicit_error():
    state = get_initial_world_state(map_id="hazard_lab")
    state["intent"] = "INTERACT"
    state["intent_context"] = {
        "action_actor": "player",
        "action_target": "",
    }

    result = mechanics_node(state)

    assert any("未指定目标" in str(line) for line in result.get("journal_events", []))
