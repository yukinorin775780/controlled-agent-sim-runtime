import asyncio

from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.input import input_node
from core.systems import mechanics
from core.systems.world_init import get_initial_world_state


def _lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["flags"] = {}
    state["journal_events"] = []
    state["pending_events"] = []
    state["speaker_responses"] = []
    return state


def _reveal_trap_with_scout(state: dict) -> dict:
    state = {
        **state,
        "user_input": "前面的走廊安全吗？",
        "intent": "chat",
        "pending_events": [],
        "speaker_responses": [],
    }
    dm_patch = {
        "intent": "CHAT",
        "current_speaker": "scout",
        "speaker_queue": [],
        "intent_context": {
            "trap_awareness_context": {
                "topic": "poison_trap",
                "trap_id": "gas_trap_1",
                "actor_id": "scout",
                "can_detect": True,
                "can_disarm": True,
                "revealed": False,
                "disarmed": False,
                "triggered": False,
            }
        },
    }
    after_dm = {**state, **dm_patch}
    invocation_patch = asyncio.run(actor_invocation_node(after_dm))
    after_invocation = {**after_dm, **invocation_patch}
    drain_patch = event_drain_node(after_invocation)
    return {**after_invocation, **drain_patch}


def test_act2_scout_warning_sets_act2_perception_flags():
    state = _reveal_trap_with_scout(_lab_state())

    assert state["flags"]["act2_corridor_entered"] is True
    assert state["flags"]["act2_scout_perception_checked"] is True
    assert state["flags"]["act2_scout_perception_success"] is True
    assert state["flags"]["act2_gas_trap_revealed"] is True
    assert state["flags"]["hazard_lab_poison_trap_revealed"] is True
    assert any("[陷阱感知] scout -> gas_trap_1" in line for line in state["journal_events"])


def test_act2_scout_disarm_success_sets_act2_flags():
    state = _reveal_trap_with_scout(_lab_state())
    result = mechanics.execute_disarm_action(
        {
            **state,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
            },
        }
    )

    assert result["flags"]["act2_scout_ordered_to_disarm"] is True
    assert result["flags"]["act2_disarm_actor"] == "scout"
    assert result["flags"]["act2_disarm_attempted"] is True
    assert result["flags"]["act2_disarm_success"] is True
    assert result["flags"]["act2_gas_trap_disarmed"] is True
    assert result["environment_objects"]["gas_trap_1"]["status"] == "disabled"


def test_act2_corridor_move_near_lab_door_does_not_trigger_act4_poison_valve():
    state = _reveal_trap_with_scout(_lab_state())
    disarmed = mechanics.execute_disarm_action(
        {
            **state,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
            },
        }
    )
    move_result = mechanics.execute_move_action(
        {
            **state,
            **disarmed,
            "intent": "MOVE",
            "intent_context": {"action_actor": "player", "action_target": "4,8"},
        }
    )

    assert move_result["entities"]["player"]["x"] == 4
    assert move_result["entities"]["player"]["y"] == 8
    poison_valve = move_result["environment_objects"]["poison_valve"]
    potion_tank = move_result["environment_objects"]["potion_tank"]
    assert poison_valve["status"] == "armed"
    assert poison_valve.get("room_id") == "room_d_lab"
    assert potion_tank.get("room_id") == "room_d_lab"
    assert poison_valve["y"] < 8
    assert potion_tank["y"] < 8
    assert not any("poison_valve" in line or "毒气阀门" in line for line in move_result["journal_events"])


def test_act2_scout_disarm_failure_triggers_poison_state():
    state = _reveal_trap_with_scout(_lab_state())
    state["flags"]["hazard_lab_force_trap_disarm_failure"] = True
    result = mechanics.execute_disarm_action(
        {
            **state,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
            },
        }
    )

    assert result["flags"]["act2_disarm_attempted"] is True
    assert result["flags"]["act2_disarm_success"] is False
    assert result["flags"]["act2_gas_trap_triggered"] is True
    assert result["flags"]["act2_gas_trap_damage_applied"] is True
    assert result["entities"]["player"]["status_effects"][0]["type"] == "poisoned"
    assert any("[陷阱解除失败] scout -> gas_trap_1" in line for line in result["journal_events"])
    assert any("[毒气陷阱] gas_trap_1 triggered" in line for line in result["journal_events"])


def test_input_routes_scout_natural_language_disarm_to_gas_trap():
    state = {
        **_lab_state(),
        "user_input": "侦察员，解除陷阱。",
        "intent": "chat",
    }

    patch = input_node(state)

    assert patch["intent"] == "DISARM"
    assert patch["target"] == "gas_trap_1"
    assert patch["intent_context"]["action_actor"] == "scout"
    assert patch["intent_context"]["action_target"] == "gas_trap_1"


def test_world_init_has_corridor_lab_door_with_key_and_lockpick_contract():
    state = _lab_state()
    door = state["entities"]["door_b_to_d"]

    assert door["entity_type"] == "door"
    assert door["is_locked"] is True
    assert door["key_required"] == "lab_key"
    assert door["lockpick_dc"] == 15


def test_corridor_lab_door_interact_without_key_reports_key_gate():
    state = _lab_state()
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["player_inventory"].pop("lab_key", None)

    result = mechanics.execute_interact_action(
        {
            **state,
            "intent": "INTERACT",
            "intent_context": {
                "action_actor": "player",
                "action_target": "door_b_to_d",
            },
        }
    )

    assert result["flags"]["act2_corridor_exit_door_inspected"] is True
    assert result["flags"]["act2_corridor_exit_requires_key"] is True
    assert result["flags"]["act2_secret_study_hint_given"] is True
    assert result["flags"]["act2_secret_study_route_unlocked"] is True
    assert result["raw_roll_data"]["result"]["result_type"] == "MISSING_KEY"
    assert any("lab_key" in line for line in result["journal_events"])
    assert any("书房" in line or "入口" in line for line in result["journal_events"])


def test_corridor_lab_door_check_does_not_auto_lockpick_when_dm_returns_unlock():
    state = _lab_state()
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["player_inventory"].pop("lab_key", None)

    result = mechanics.execute_unlock_action(
        {
            **state,
            "user_input": "检查 B-D 门。",
            "intent": "UNLOCK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "door_b_to_d",
            },
        }
    )

    assert result["flags"]["act2_corridor_exit_door_inspected"] is True
    assert result["flags"]["act2_corridor_exit_requires_key"] is True
    assert result["flags"]["act2_secret_study_hint_given"] is True
    assert result["flags"]["act2_secret_study_route_unlocked"] is True
    assert "act2_corridor_exit_lockpick_attempted" not in result["flags"]
    assert result["raw_roll_data"]["result"]["result_type"] == "INSPECT_REQUIRES_EXPLICIT_LOCKPICK"


def test_input_routes_lab_door_check_to_interact_not_unlock():
    patch = input_node(
        {
            **_lab_state(),
            "user_input": "检查 B-D 门。",
            "intent": "chat",
        }
    )

    assert patch["intent"] == "INTERACT"
    assert patch["target"] == "door_b_to_d"
    assert patch["intent_context"]["action"] == "inspect_lab_door"


def test_negative_lockpick_text_downgrades_to_inspect():
    state = _lab_state()
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["player_inventory"].pop("lab_key", None)
    text = "检查 door_b_to_d，不要撬锁。"

    input_patch = input_node({**state, "user_input": text, "intent": "chat"})
    dm_patch = asyncio.run(dm_node({**state, "user_input": text, "intent": "chat"}))
    result = mechanics.execute_unlock_action(
        {
            **state,
            "user_input": text,
            "intent": "UNLOCK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "door_b_to_d",
            },
        }
    )

    assert input_patch["intent"] == "INTERACT"
    assert input_patch["target"] == "door_b_to_d"
    assert dm_patch["intent"] == "INTERACT"
    assert dm_patch["intent_context"]["action_target"] == "door_b_to_d"
    assert result["raw_roll_data"]["result"]["result_type"] == "INSPECT_REQUIRES_EXPLICIT_LOCKPICK"
    assert "act2_corridor_exit_lockpick_attempted" not in result["flags"]


def test_explicit_lockpick_still_routes_to_unlock():
    patch = input_node(
        {
            **_lab_state(),
            "user_input": "Scout lockpick the door_b_to_d door.",
            "intent": "chat",
        }
    )

    assert patch["intent"] == "UNLOCK"
    assert patch["target"] == "door_b_to_d"
    assert patch["intent_context"]["action"] == "lockpick_lab_door"


def test_corridor_lab_door_lockpick_success_skips_secret_study():
    state = _lab_state()
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["flags"]["hazard_lab_force_lockpick_success"] = True
    state["player_inventory"].pop("lab_key", None)

    result = mechanics.execute_unlock_action(
        {
            **state,
            "intent": "UNLOCK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "door_b_to_d",
            },
        }
    )

    assert result["flags"]["act2_corridor_exit_lockpick_attempted"] is True
    assert result["flags"]["act2_corridor_exit_lockpick_success"] is True
    assert result["flags"]["act2_lockpick_success_route_to_boss"] is True
    assert result["flags"].get("hazard_lab_diary_decoded") is None
    assert result["entities"]["door_b_to_d"]["is_open"] is True
    assert result["entities"]["door_b_to_d"]["is_locked"] is False


def test_corridor_lab_door_lockpick_failure_hints_secret_study():
    state = _lab_state()
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["flags"]["hazard_lab_force_lockpick_failure"] = True
    state["player_inventory"].pop("lab_key", None)

    result = mechanics.execute_unlock_action(
        {
            **state,
            "intent": "UNLOCK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "door_b_to_d",
            },
        }
    )

    assert result["flags"]["act2_corridor_exit_lockpick_attempted"] is True
    assert result["flags"]["act2_corridor_exit_lockpick_success"] is False
    assert result["flags"]["act2_secret_study_hint_given"] is True
    assert result["flags"]["act2_secret_study_route_unlocked"] is True
    assert any("密道" in line or "别的入口" in line for line in result["journal_events"])
