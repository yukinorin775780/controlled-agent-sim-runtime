import asyncio
import copy

from core.actors.builders import build_actor_view
from core.campaigns.hazard_lab import (
    detect_poison_trap_trigger_context,
    detect_trap_awareness_context,
)
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.input import input_node
from core.systems import mechanics
from core.systems.world_init import get_initial_world_state


def _build_lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["flags"] = {}
    state["journal_events"] = []
    state["pending_events"] = []
    state["speaker_responses"] = []
    return state


def _open_corridor_and_move_near_trap(state: dict) -> dict:
    state = copy.deepcopy(state)
    for bucket_name in ("entities", "environment_objects"):
        door = state.get(bucket_name, {}).get("door_a_to_b")
        if isinstance(door, dict):
            door["is_open"] = True
            door["status"] = "open"
    player = state.get("entities", {}).get("player")
    if isinstance(player, dict):
        player["x"] = 5
        player["y"] = 12
    return state


def _warn_about_trap(state: dict) -> dict:
    state = _open_corridor_and_move_near_trap(state)
    state = {
        **state,
        "user_input": "前面安全吗？继续往前走。",
        "intent": "chat",
        "pending_events": [],
        "speaker_responses": [],
    }
    dm_patch = asyncio.run(dm_node(state))
    after_dm = {**state, **dm_patch}
    invocation_patch = asyncio.run(actor_invocation_node(after_dm))
    after_invocation = {**after_dm, **invocation_patch}
    drain_patch = event_drain_node(after_invocation)
    return {**after_invocation, **drain_patch}


def test_trap_awareness_helper_and_initial_actor_view_do_not_leak_hidden_trap():
    state = _build_lab_state()

    initial_context = detect_trap_awareness_context(state, "继续往前走", {})
    player_view = build_actor_view(state, "player")
    scout_view = build_actor_view(state, "scout")

    assert initial_context is None
    context = detect_trap_awareness_context(
        _open_corridor_and_move_near_trap(state),
        "继续往前走",
        {},
    )
    assert context is not None
    assert context["topic"] == "poison_trap"
    assert context["trap_id"] == "gas_trap_1"
    assert context["can_detect"] is True
    assert context["revealed"] is False
    assert "gas_trap_1" not in player_view.visible_environment_objects
    assert "gas_trap_1" not in scout_view.visible_environment_objects


def test_trap_awareness_helper_noops_outside_hazard_lab():
    state = _build_lab_state()
    state["map_data"]["id"] = "training_range"

    assert detect_trap_awareness_context(state, "继续往前走", {}) is None


def test_trap_awareness_requires_corridor_access_proximity_and_single_check():
    state = _build_lab_state()
    assert detect_trap_awareness_context(state, "前面的走廊安全吗？", {}) is None

    door_open_far = copy.deepcopy(state)
    door_open_far["entities"]["door_a_to_b"]["is_open"] = True
    door_open_far["entities"]["door_a_to_b"]["status"] = "open"
    door_open_far["entities"]["player"]["x"] = 2
    door_open_far["entities"]["player"]["y"] = 2
    assert detect_trap_awareness_context(door_open_far, "前面的走廊安全吗？", {}) is None

    near = _open_corridor_and_move_near_trap(state)
    context = detect_trap_awareness_context(near, "前面的走廊安全吗？", {})
    assert context is not None
    assert context["trap_id"] == "gas_trap_1"

    near["flags"]["act2_scout_perception_checked"] = True
    assert detect_trap_awareness_context(near, "前面的走廊安全吗？", {}) is None


def test_poison_trap_trigger_helper_requires_hazard_lab():
    state = _build_lab_state()
    state["map_data"]["id"] = "training_range"

    context = detect_poison_trap_trigger_context(
        state,
        "",
        {"action_target": "gas_trap_1", "source": "trap_trigger"},
    )

    assert context is None


def test_scout_warning_reveals_poison_trap_and_writes_journal():
    state = _warn_about_trap(_build_lab_state())

    assert state["flags"]["hazard_lab_poison_trap_revealed"] is True
    assert state["flags"]["scout_detected_gas_trap"]["value"] is True
    assert state["environment_objects"]["gas_trap_1"]["is_hidden"] is False
    assert state["entities"]["gas_trap_1"]["is_hidden"] is False
    assert any("[陷阱感知] scout -> gas_trap_1" in line for line in state["journal_events"])
    assert any("毒气压力板" in text for _, text in state["speaker_responses"])


def test_scout_disarms_revealed_trap_and_safe_crossing_does_not_poison():
    warned = _warn_about_trap(_build_lab_state())

    disarmed = mechanics.execute_disarm_action(
        {
            **warned,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
            },
        }
    )
    crossed = mechanics.execute_move_action(
        {
            **warned,
            **disarmed,
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "5,11",
            },
        }
    )

    assert disarmed["flags"]["hazard_lab_poison_trap_disarmed"] is True
    assert disarmed["environment_objects"]["gas_trap_1"]["status"] == "disabled"
    assert "gas_trap_1" not in disarmed["entities"]
    assert any("[陷阱解除] scout -> gas_trap_1" in line for line in disarmed["journal_events"])
    assert not any(effect.get("type") == "poisoned" for effect in crossed["entities"]["player"]["status_effects"])


def test_ignored_poison_trap_triggers_poison_once():
    state = _build_lab_state()
    triggered = mechanics.execute_move_action(
        {
            **state,
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "5,11",
            },
        }
    )
    repeated = mechanics.execute_move_action(
        {
            **state,
            **triggered,
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "5,11",
            },
        }
    )

    first_effects = triggered["entities"]["player"]["status_effects"]
    repeated_effects = repeated["entities"]["player"]["status_effects"]
    assert triggered["flags"]["hazard_lab_poison_trap_triggered"] is True
    assert triggered["environment_objects"]["gas_trap_1"]["status"] == "triggered"
    assert any(effect.get("type") == "poisoned" for effect in first_effects)
    assert sum(1 for effect in repeated_effects if effect.get("type") == "poisoned") == 1
    assert not any("[毒气陷阱] gas_trap_1 triggered" in line for line in repeated["journal_events"])


def test_structured_trap_trigger_action_writes_flags_status_and_poison():
    state = _build_lab_state()

    triggered = mechanics.execute_trigger_trap_action(
        {
            **state,
            "intent": "TRIGGER_TRAP",
            "target": "gas_trap_1",
            "source": "trap_trigger",
            "intent_context": {
                "action_actor": "player",
                "action_target": "gas_trap_1",
                "source": "trap_trigger",
            },
        }
    )

    player_effects = triggered["entities"]["player"]["status_effects"]
    poisoned = [effect for effect in player_effects if effect.get("type") == "poisoned"]
    assert any("[毒气陷阱] gas_trap_1 triggered" in line for line in triggered["journal_events"])
    assert triggered["flags"]["hazard_lab_poison_trap_triggered"] is True
    assert triggered["environment_objects"]["gas_trap_1"]["status"] == "triggered"
    assert len(poisoned) == 1
    assert poisoned[0]["duration"] == 3


def test_repeated_structured_trap_trigger_does_not_duplicate_poison():
    state = _build_lab_state()
    trigger_state = {
        "intent": "TRIGGER_TRAP",
        "target": "gas_trap_1",
        "source": "trap_trigger",
        "intent_context": {
            "action_actor": "player",
            "action_target": "gas_trap_1",
            "source": "trap_trigger",
        },
    }
    triggered = mechanics.execute_trigger_trap_action({**state, **trigger_state})
    repeated = mechanics.execute_trigger_trap_action({**state, **triggered, **trigger_state})

    repeated_effects = repeated["entities"]["player"]["status_effects"]
    assert sum(1 for effect in repeated_effects if effect.get("type") == "poisoned") == 1
    assert not any("[毒气陷阱] gas_trap_1 triggered" in line for line in repeated["journal_events"])


def test_teammate_entering_poison_trap_zone_triggers_trap():
    state = _build_lab_state()

    triggered = mechanics.execute_move_action(
        {
            **state,
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "5,11",
            },
        }
    )

    scout_effects = triggered["entities"]["scout"]["status_effects"]
    assert triggered["flags"]["hazard_lab_poison_trap_triggered"] is True
    assert any(effect.get("type") == "poisoned" for effect in scout_effects)


def test_disarmed_poison_trap_does_not_trigger():
    warned = _warn_about_trap(_build_lab_state())
    disarmed = mechanics.execute_disarm_action(
        {
            **warned,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
            },
        }
    )

    triggered = mechanics.execute_trigger_trap_action(
        {
            **warned,
            **disarmed,
            "intent": "TRIGGER_TRAP",
            "target": "gas_trap_1",
            "source": "trap_trigger",
            "intent_context": {
                "action_actor": "player",
                "action_target": "gas_trap_1",
                "source": "trap_trigger",
            },
        }
    )

    assert disarmed["flags"]["hazard_lab_poison_trap_disarmed"] is True
    assert triggered["flags"]["hazard_lab_poison_trap_disarmed"] is True
    assert "hazard_lab_poison_trap_triggered" not in triggered["flags"]
    assert not any(effect.get("type") == "poisoned" for effect in triggered["entities"]["player"]["status_effects"])


def test_forced_scout_disarm_failure_triggers_trap_without_disarmed_flag():
    warned = _warn_about_trap(_build_lab_state())

    failed = mechanics.execute_disarm_action(
        {
            **warned,
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "gas_trap_1",
                "force_disarm_failure": True,
            },
        }
    )

    player_effects = failed["entities"]["player"]["status_effects"]
    assert any("[陷阱解除失败] scout -> gas_trap_1" in line for line in failed["journal_events"])
    assert any("[毒气陷阱] gas_trap_1 triggered" in line for line in failed["journal_events"])
    assert failed["flags"]["hazard_lab_poison_trap_triggered"] is True
    assert "hazard_lab_poison_trap_disarmed" not in failed["flags"]
    assert failed["environment_objects"]["gas_trap_1"]["status"] == "triggered"
    assert any(effect.get("type") == "poisoned" and effect.get("duration") == 3 for effect in player_effects)


def test_structured_interact_trap_trigger_is_not_downgraded_to_awareness():
    state = _build_lab_state()
    state.update(
        {
            "user_input": "",
            "intent": "INTERACT",
            "target": "gas_trap_1",
            "source": "trap_trigger",
            "intent_context": {
                "action_actor": "player",
                "action_target": "gas_trap_1",
                "source": "trap_trigger",
            },
            "pending_events": [],
            "speaker_responses": [],
        }
    )

    dm_patch = asyncio.run(dm_node(state))

    assert dm_patch["intent"] == "TRIGGER_TRAP"
    assert dm_patch["intent_context"]["action_target"] == "gas_trap_1"
    assert dm_patch["intent_context"]["source"] == "trap_trigger"


def test_input_node_normalizes_empty_structured_interact_trap_trigger():
    patch = input_node(
        {
            **_build_lab_state(),
            "user_input": "",
            "intent": "INTERACT",
            "target": "gas_trap_1",
            "source": "trap_trigger",
        }
    )

    assert patch["intent"] == "TRIGGER_TRAP"
    assert patch["intent_context"]["action_target"] == "gas_trap_1"
    assert patch["intent_context"]["source"] == "trap_trigger"


def test_deepcopy_state_not_required_for_warning_path():
    state = _build_lab_state()
    original = copy.deepcopy(state)

    _warn_about_trap(state)

    assert original["entities"]["gas_trap_1"]["is_hidden"] is True
