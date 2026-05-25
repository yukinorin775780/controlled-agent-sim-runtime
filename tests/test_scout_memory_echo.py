import asyncio

from core.actors.builders import build_actor_view
from core.campaigns.hazard_lab import detect_scout_memory_echo_context
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.systems import mechanics
from core.systems.world_init import get_initial_world_state


def _build_lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["flags"] = {}
    state["journal_events"] = []
    state["pending_events"] = []
    state["speaker_responses"] = []
    state.setdefault("actor_runtime_state", {})
    return state


def _with_rebuke_history(state: dict) -> dict:
    out = {**state}
    out["flags"] = {
        **dict(state.get("flags") or {}),
        "hazard_lab_scout_mocked_gatekeeper": True,
        "hazard_lab_player_sided_with_scout": False,
    }
    out["actor_runtime_state"] = {
        **dict(state.get("actor_runtime_state") or {}),
        "scout": {"memory_notes": ["玩家当众训斥了我，我会记住这笔账。"]},
    }
    out["entities"] = {
        **dict(state.get("entities") or {}),
        "scout": {**dict(state["entities"]["scout"]), "affection": 7},
    }
    return out


def _with_side_history(state: dict) -> dict:
    out = {**state}
    out["flags"] = {
        **dict(state.get("flags") or {}),
        "hazard_lab_scout_mocked_gatekeeper": True,
        "hazard_lab_player_sided_with_scout": True,
    }
    out["actor_runtime_state"] = {
        **dict(state.get("actor_runtime_state") or {}),
        "scout": {"memory_notes": ["玩家与我一起嘲笑了 Gatekeeper，这种默契让我满意。"]},
    }
    out["entities"] = {
        **dict(state.get("entities") or {}),
        "scout": {**dict(state["entities"]["scout"]), "affection": 7},
    }
    return out


def _run_chat_turn(state: dict, user_input: str) -> dict:
    turn_state = {
        **state,
        "user_input": user_input,
        "intent": "chat",
        "pending_events": [],
        "speaker_responses": [],
    }
    dm_patch = asyncio.run(dm_node(turn_state))
    after_dm = {**turn_state, **dm_patch}
    invocation_patch = asyncio.run(actor_invocation_node(after_dm))
    after_invocation = {**after_dm, **invocation_patch}
    drain_patch = event_drain_node(after_invocation)
    return {**after_invocation, **drain_patch}


def test_memory_echo_helper_noops_without_prior_history():
    state = _build_lab_state()

    assert detect_scout_memory_echo_context(state, "侦察员，你怎么看？", {}) is None


def test_rebuke_history_surfaces_resentful_response_journal_and_flag_without_affection_delta():
    state = _with_rebuke_history(_build_lab_state())
    result = _run_chat_turn(state, "侦察员，你怎么看这件事？")

    response_text = "\n".join(text for _, text in result.get("speaker_responses", []))
    assert "现在又需要我" in response_text
    assert "闭嘴" in response_text
    assert any(
        "[记忆回响] scout -> rebuked_by_player" in line
        for line in result.get("journal_events", [])
    )
    assert result["flags"]["hazard_lab_scout_memory_echo_seen"] is True
    assert result["flags"]["hazard_lab_scout_rebuke_echo_seen"] is True
    assert result["entities"]["scout"]["affection"] == 7


def test_side_history_surfaces_complicit_response_and_journal():
    state = _with_side_history(_build_lab_state())
    result = _run_chat_turn(state, "Scout, what do you think?")

    response_text = "\n".join(text for _, text in result.get("speaker_responses", []))
    assert "一起" in response_text
    assert "默契" in response_text
    assert any(
        "[记忆回响] scout -> sided_with_player" in line
        for line in result.get("journal_events", [])
    )
    assert result["flags"]["hazard_lab_scout_complicity_echo_seen"] is True
    assert result["entities"]["scout"]["affection"] == 7


def test_rebuke_echo_does_not_block_key_guidance():
    state = _with_rebuke_history(_build_lab_state())
    result = _run_chat_turn(state, "侦察员，怎么打开实验室门？")

    response_text = "\n".join(text for _, text in result.get("speaker_responses", []))
    assert "现在又需要我" in response_text
    assert "lab_key" in response_text
    assert "书房" in response_text
    assert "撬锁" in response_text
    assert "lab_key" not in result.get("player_inventory", {})


def test_rebuke_echo_does_not_block_scout_trap_disarm():
    state = _with_rebuke_history(_build_lab_state())
    state["flags"] = {
        **dict(state.get("flags") or {}),
        "hazard_lab_poison_trap_revealed": True,
        "scout_detected_gas_trap": {"value": True},
    }
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

    assert disarmed["flags"]["hazard_lab_poison_trap_disarmed"] is True
    assert disarmed["environment_objects"]["gas_trap_1"]["status"] == "disabled"
    assert any(
        "[记忆回响] scout -> rebuked_by_player" in line
        for line in disarmed.get("journal_events", [])
    )


def test_scout_private_memory_does_not_leak_to_other_actor_views():
    state = _with_rebuke_history(_build_lab_state())

    analyst_view = build_actor_view(state, "analyst")
    tactician_view = build_actor_view(state, "tactician")

    assert "记住这笔账" not in repr(analyst_view)
    assert "记住这笔账" not in repr(tactician_view)
    assert not hasattr(analyst_view.other_entities["scout"], "memory_notes")
    assert not hasattr(tactician_view.other_entities["scout"], "memory_notes")
