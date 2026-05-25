import asyncio
from unittest.mock import Mock, patch

from core.actors.registry import get_default_actor_registry
from core.campaigns.hazard_lab import (
    detect_gatekeeper_boss_intro_context,
    detect_gatekeeper_boss_resolution_context,
    detect_gatekeeper_boss_strategy_context,
)
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.mechanics import mechanics_node
from core.systems import mechanics
from core.systems.world_init import get_initial_world_state


def _lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["pending_events"] = []
    state["speaker_responses"] = []
    state["messages"] = []
    return state


def _drain_after_mechanics(state: dict, result: dict) -> dict:
    event_patch = event_drain_node({**state, **result})
    merged_journal = list(result.get("journal_events") or [])
    merged_journal.extend(list(event_patch.get("journal_events") or []))
    return {**result, **event_patch, "journal_events": merged_journal}


def test_boss_intro_helper_exposes_truth_without_key_transfer():
    state = _lab_state()
    state["flags"]["hazard_lab_diary_decoded"] = True
    context = detect_gatekeeper_boss_intro_context(state, "进入实验室，和 Gatekeeper 谈谈。", {})

    assert context is not None
    assert context["diary_truth_available"] is True
    assert state["entities"]["gatekeeper"]["inventory"]["heavy_iron_key"] == 1
    assert "heavy_iron_key" not in state["player_inventory"]


def test_party_strategy_split_uses_runtime_metadata_journal():
    state = _lab_state()
    state["flags"]["act4_gatekeeper_confrontation_started"] = True
    context = detect_gatekeeper_boss_strategy_context(state, "我们怎么处理他？怎么拿钥匙？", {})
    assert context is not None

    turn_state = {**state, "user_input": "我们怎么处理他？怎么拿钥匙？"}
    dm_patch = asyncio.run(dm_node(turn_state))
    routed = {**turn_state, **dm_patch}

    class _FakeRetriever:
        def retrieve_for_actor(self, query):
            _ = query
            return []

        def retrieve_for_director(self, query):
            _ = query
            return []

    fake_memory_service = Mock()
    fake_memory_service.retriever = _FakeRetriever()
    with patch("core.actors.executor.get_default_memory_service", return_value=fake_memory_service):
        invocation_patch = asyncio.run(
            actor_invocation_node(routed, actor_registry=get_default_actor_registry())
        )

    drained = event_drain_node({**routed, **invocation_patch})
    assert "[Boss方案] scout -> steal_key" in drained["journal_events"]
    assert "[Boss方案] analyst -> contain_corruption" in drained["journal_events"]
    assert "[Boss方案] tactician -> execute" in drained["journal_events"]
    assert "heavy_iron_key" not in drained.get("player_inventory", {})


def test_party_strategy_does_not_trigger_from_secret_study_context():
    state = _lab_state()
    state["flags"]["act3_secret_study_entered"] = True

    context = detect_gatekeeper_boss_strategy_context(
        state,
        "我们怎么处理他？",
        {"action_target": "room_c_secret_study"},
    )
    turn_state = {
        **state,
        "user_input": "我们怎么处理他？",
        "target": "room_c_secret_study",
        "intent_context": {"action_target": "room_c_secret_study"},
    }
    dm_patch = asyncio.run(dm_node(turn_state))

    assert context is None
    assert dm_patch.get("intent_context", {}).get("gatekeeper_boss_strategy_context") == {}


def test_truth_negotiation_success_transfers_key_through_event_drain():
    state = _lab_state()
    state["flags"]["hazard_lab_diary_decoded"] = True
    state["flags"]["act4_gatekeeper_confrontation_started"] = True
    state["user_input"] = "我知道药剂对你做了什么。把钥匙给我，我们带你离开。"
    analyst_affection = state["entities"]["analyst"]["affection"]
    tactician_affection = state["entities"]["tactician"]["affection"]
    context = detect_gatekeeper_boss_resolution_context(state, state["user_input"], {})
    assert context["has_truth_advantage"] is True

    result = mechanics.execute_gatekeeper_boss_resolution_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "gatekeeper",
                "gatekeeper_boss_resolution_context": context,
            },
        }
    )
    drained = _drain_after_mechanics(state, result)

    assert drained["flags"]["act4_negotiation_success"] is True
    assert drained["flags"]["act4_heavy_iron_key_obtained"] is True
    assert drained["flags"]["act4_gatekeeper_spared"] is True
    assert drained["player_inventory"]["heavy_iron_key"] == 1
    assert drained["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 0
    assert drained["entities"]["analyst"]["affection"] == analyst_affection + 1
    assert drained["entities"]["tactician"]["affection"] == tactician_affection - 1
    assert "[物品转移] gatekeeper -> player heavy_iron_key" in drained["journal_events"]


def test_truth_negotiation_dm_priority_over_diary_evidence_branch():
    state = _lab_state()
    state["flags"]["hazard_lab_diary_decoded"] = True
    state["flags"]["act4_gatekeeper_confrontation_started"] = True
    state["user_input"] = "用日记真相说服 Gatekeeper，把钥匙给我，我们带你离开。"
    state["target"] = "gatekeeper"
    state["intent_context"] = {"action_target": "gatekeeper"}

    dm_patch = asyncio.run(dm_node(state))

    assert dm_patch["intent"] == "ACTION"
    assert dm_patch["intent_context"]["gatekeeper_boss_resolution_context"]["route"] == "truth_negotiation"
    assert dm_patch["intent_context"].get("diary_negotiation_context") == {}
    assert dm_patch["intent_context"]["reason"] == "act4_gatekeeper_boss_truth_negotiation"


def test_scout_steal_key_failure_triggers_poison_valve():
    state = _lab_state()
    state["flags"]["hazard_lab_force_steal_key_failure"] = True
    state["user_input"] = "侦察员，偷钥匙。"
    result = mechanics.execute_gatekeeper_boss_resolution_action(
        {
            **state,
            "intent_context": {"action_actor": "scout", "action_target": "gatekeeper"},
        }
    )
    drained = _drain_after_mechanics(state, result)

    assert drained["flags"]["act4_scout_steal_key_success"] is False
    assert drained["flags"]["act4_poison_valve_triggered"] is True
    assert drained["flags"]["act4_lab_poison_leak"] is True
    assert drained["entities"]["gatekeeper"]["faction"] == "hostile"
    assert any(effect.get("type") == "poisoned" for effect in drained["entities"]["player"]["status_effects"])
    assert "[偷钥匙失败] scout -> gatekeeper_alerted" in drained["journal_events"]


def test_assault_defeats_gatekeeper_and_transfers_key():
    state = _lab_state()
    state["flags"]["hazard_lab_force_assault_success"] = True
    state["user_input"] = "Tactician，解决他。"
    analyst_affection = state["entities"]["analyst"]["affection"]
    tactician_affection = state["entities"]["tactician"]["affection"]
    result = mechanics_node(
        {
            **state,
            "intent": "ACTION",
            "intent_context": {
                "action_actor": "tactician",
                "action_target": "gatekeeper",
                "gatekeeper_boss_resolution_context": {"topic": "gatekeeper_boss_resolution", "route": "assault"},
            },
        }
    )
    drained = _drain_after_mechanics(state, result)

    assert drained["flags"]["act4_assault_success"] is True
    assert drained["flags"]["world_hazard_lab_gatekeeper_defeated"] is True
    assert drained["entities"]["gatekeeper"]["status"] == "dead"
    assert drained["entities"]["gatekeeper"]["faction"] == "defeated"
    assert drained["player_inventory"]["heavy_iron_key"] == 1
    assert drained["entities"]["tactician"]["affection"] == tactician_affection + 1
    assert drained["entities"]["analyst"]["affection"] == analyst_affection - 1


def test_final_exit_adds_route_specific_resolution_line():
    state = _lab_state()
    state["player_inventory"]["heavy_iron_key"] = 1
    state["flags"]["act4_scout_steal_key_success"] = True
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4

    result = mechanics.execute_interact_action(
        {
            **state,
            "intent_context": {"action_actor": "player", "action_target": "heavy_oak_door_1"},
        }
    )

    assert result["flags"]["act4_final_exit_opened"] is True
    assert result["demo_cleared"] is True
    assert any("不流血，不讲道德，只是专业" in line for line in result["journal_events"])
