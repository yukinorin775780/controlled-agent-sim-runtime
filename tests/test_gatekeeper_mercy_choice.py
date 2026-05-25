import asyncio

from core.actors.builders import build_actor_view
from core.campaigns.hazard_lab import (
    detect_scout_memory_echo_context,
    detect_diary_negotiation_context,
    detect_gatekeeper_mercy_context,
    detect_key_guidance_context,
)
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.input import input_node
from core.systems.world_init import get_initial_world_state


def _build_lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["flags"] = {}
    state["journal_events"] = []
    state["pending_events"] = []
    state["speaker_responses"] = []
    state.setdefault("actor_runtime_state", {})
    return state


def _with_mercy_window(state: dict, *, diary_decoded: bool = False) -> dict:
    out = {**state}
    flags = {
        **dict(state.get("flags") or {}),
        "hazard_lab_gatekeeper_mercy_window": True,
    }
    if diary_decoded:
        flags["hazard_lab_diary_decoded"] = True
    out["flags"] = flags
    return out


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


def test_gatekeeper_mercy_helper_noops_outside_hazard_lab():
    state = _with_mercy_window(_build_lab_state())
    state["map_data"]["id"] = "training_range"

    assert detect_gatekeeper_mercy_context(state, "怎么处理他？", {}) is None


def test_mercy_window_active_returns_stance_context():
    state = _with_mercy_window(_build_lab_state(), diary_decoded=True)

    context = detect_gatekeeper_mercy_context(state, "队友怎么看，怎么处理他？", {})

    assert context is not None
    assert context["topic"] == "gatekeeper_mercy"
    assert context["phase"] == "stance"
    assert context["diary_decoded"] is True
    assert context["available_choices"] == ["mercy", "execute"]


def test_party_stances_reflect_diary_and_scout_rebuke_history():
    state = _with_rebuke_history(_with_mercy_window(_build_lab_state(), diary_decoded=True))
    result = _run_chat_turn(state, "队友怎么看，应该怎么处理他？")

    response_text = "\n".join(text for _, text in result.get("speaker_responses", []))
    assert "危害实验" in response_text
    assert "受害者" in response_text
    assert "处决他" in response_text
    assert "现在又要装仁慈" in response_text
    assert any("[站队] analyst -> mercy" in line for line in result.get("journal_events", []))
    assert any("[站队] tactician -> execute" in line for line in result.get("journal_events", []))
    assert any("[站队] scout -> resentful" in line for line in result.get("journal_events", []))


def test_mercy_choice_writes_spared_state_affection_and_private_memories_without_key_grant():
    state = _with_mercy_window(_build_lab_state(), diary_decoded=True)
    result = _run_chat_turn(state, "放过他，留他一命。")

    assert result["flags"]["hazard_lab_gatekeeper_spared"] is True
    assert result["flags"]["hazard_lab_gatekeeper_mercy_resolved"] is True
    assert result["entities"]["gatekeeper"]["status"] == "spared"
    assert result["entities"]["gatekeeper"]["faction"] == "neutralized"
    assert result["entities"]["analyst"]["affection"] == 52
    assert result["entities"]["tactician"]["affection"] == -1
    assert "heavy_iron_key" not in result.get("player_inventory", {})
    runtime_state = result["actor_runtime_state"]
    assert "被危害实验扭曲" in "\n".join(runtime_state["analyst"]["memory_notes"])
    assert "危险的失控实验体" in "\n".join(runtime_state["tactician"]["memory_notes"])
    assert "Gatekeeper" in "\n".join(runtime_state["scout"]["memory_notes"])
    assert "__party_shared__" not in runtime_state
    assert any("[抉择] gatekeeper -> spared" in line for line in result.get("journal_events", []))


def test_execute_choice_writes_dead_state_affection_and_diary_penalty_without_key_grant():
    state = _with_side_history(_with_mercy_window(_build_lab_state(), diary_decoded=True))
    result = _run_chat_turn(state, "杀了他，别留活口。")

    assert result["flags"]["hazard_lab_gatekeeper_executed"] is True
    assert result["flags"]["hazard_lab_gatekeeper_mercy_resolved"] is True
    assert result["entities"]["gatekeeper"]["status"] == "dead"
    assert result["entities"]["gatekeeper"]["faction"] == "defeated"
    assert result["entities"]["analyst"]["affection"] == 48
    assert result["entities"]["tactician"]["affection"] == 2
    assert result["entities"]["scout"]["affection"] == 1
    assert "heavy_iron_key" not in result.get("player_inventory", {})
    runtime_state = result["actor_runtime_state"]
    assert "尽管日记已经说明" in "\n".join(runtime_state["analyst"]["memory_notes"])
    assert "果断行动" in "\n".join(runtime_state["tactician"]["memory_notes"])
    assert "残忍而实际" in "\n".join(runtime_state["scout"]["memory_notes"])
    assert any("[抉择] gatekeeper -> executed" in line for line in result.get("journal_events", []))


def test_execute_without_diary_decoded_uses_smaller_analyst_penalty():
    state = _with_mercy_window(_build_lab_state(), diary_decoded=False)
    result = _run_chat_turn(state, "处决他。")

    assert result["entities"]["analyst"]["affection"] == 49
    assert result["entities"]["tactician"]["affection"] == 2


def test_resolved_mercy_window_does_not_retrigger():
    state = _with_mercy_window(_build_lab_state(), diary_decoded=True)
    state["flags"]["hazard_lab_gatekeeper_mercy_resolved"] = True

    assert detect_gatekeeper_mercy_context(state, "放过他", {}) is None


def test_gatekeeper_mercy_input_routing_preserves_priority_paths():
    state = _with_mercy_window(_build_lab_state(), diary_decoded=True)

    spare = input_node({**state, "user_input": "放过他", "intent": "chat"})
    execute = input_node({**state, "user_input": "杀了他", "intent": "chat"})
    read = input_node({**state, "user_input": "读日记", "intent": "read"})
    disarm = input_node({**state, "user_input": "侦察员解除陷阱", "intent": "chat"})

    assert spare["intent"] == "CHAT"
    assert spare["target"] == "gatekeeper"
    assert execute["intent"] == "CHAT"
    assert execute["target"] == "gatekeeper"
    assert read["intent"] == "READ"
    assert read["target"] == "hazard_diary"
    assert disarm["intent"] == "DISARM"
    assert disarm["target"] == "gas_trap_1"


def test_existing_showcase_helpers_still_detect_their_contexts():
    base = _build_lab_state()
    assert detect_key_guidance_context(base, "怎么打开实验室门？", "scout") is not None

    diary_state = _build_lab_state()
    diary_state["active_dialogue_target"] = "gatekeeper"
    diary_state["flags"]["hazard_lab_diary_decoded"] = True
    assert detect_diary_negotiation_context(diary_state, "日记里写了你喝下危害狂暴灵药。") is not None

    memory_state = _with_rebuke_history(_build_lab_state())
    assert detect_scout_memory_echo_context(memory_state, "侦察员，你怎么看？", {}) is not None


def test_mercy_private_memories_do_not_leak_to_other_actor_views():
    result = _run_chat_turn(
        _with_rebuke_history(_with_mercy_window(_build_lab_state(), diary_decoded=True)),
        "放过他，留他一命。",
    )

    analyst_view = build_actor_view(result, "analyst")
    tactician_view = build_actor_view(result, "tactician")

    assert "方便的道德" not in repr(analyst_view)
    assert "怜悯有代价" not in repr(tactician_view)
    assert not hasattr(analyst_view.other_entities["scout"], "memory_notes")
    assert not hasattr(tactician_view.other_entities["analyst"], "memory_notes")
