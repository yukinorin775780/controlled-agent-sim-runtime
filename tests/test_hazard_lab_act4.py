from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

from core.actors.registry import get_default_actor_registry
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.event_drain import event_drain_node


def _build_act4_party_turn_state(*, sided_with_scout: bool) -> dict:
    return {
        "current_speaker": "scout",
        "speaker_queue": ["analyst", "tactician"],
        "intent": "CHAT",
        "intent_context": {
            "action_actor": "player",
            "action_target": "heavy_oak_door_1",
            "act4_post_combat_banter": True,
            "player_sided_with_scout": sided_with_scout,
        },
        "user_input": "钥匙到手了，准备离开这里。",
        "turn_count": 77,
        "current_location": "hazard_lab",
        "map_data": {"id": "hazard_lab"},
        "flags": {
            "world_hazard_lab_gatekeeper_defeated": True,
            "hazard_lab_player_sided_with_scout": sided_with_scout,
        },
        "entities": {
            "player": {
                "name": "玩家",
                "faction": "player",
                "status": "alive",
                "hp": 20,
                "max_hp": 20,
                "inventory": {"heavy_iron_key": 1},
            },
            "scout": {
                "name": "Scout",
                "faction": "party",
                "status": "alive",
                "hp": 12,
                "max_hp": 12,
                "affection": 0,
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
                "faction": "hostile",
                "status": "dead",
                "hp": 0,
                "max_hp": 18,
                "inventory": {},
            },
        },
        "pending_events": [],
        "speaker_responses": [],
        "messages": [],
    }


def test_act4_party_turn_banter_uses_runtime_multi_marker():
    state = _build_act4_party_turn_state(sided_with_scout=True)

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
    assert invocation_patch["party_turn_actor_ids"] == ["scout", "analyst", "tactician"]

    drained = event_drain_node({**state, **invocation_patch})
    response_lines = [text for _speaker, text in drained.get("speaker_responses", [])]
    assert any("没拖后腿" in line for line in response_lines)
    assert any("危害" in line or "警惕" in line for line in response_lines)
    assert any("开门" in line and "继续" in line for line in response_lines)
    assert "scout" in drained["actor_runtime_state"]
    assert "analyst" in drained["actor_runtime_state"]
    assert "tactician" in drained["actor_runtime_state"]


def test_act4_scout_tone_changes_when_player_rebuked_him():
    sided_state = _build_act4_party_turn_state(sided_with_scout=True)
    rebuked_state = _build_act4_party_turn_state(sided_with_scout=False)

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
        sided_patch = asyncio.run(
            actor_invocation_node(
                sided_state,
                actor_registry=get_default_actor_registry(),
            )
        )
        rebuked_patch = asyncio.run(
            actor_invocation_node(
                rebuked_state,
                actor_registry=get_default_actor_registry(),
            )
        )

    sided_drained = event_drain_node({**sided_state, **sided_patch})
    rebuked_drained = event_drain_node({**rebuked_state, **rebuked_patch})

    sided_scout_line = [
        text
        for speaker, text in sided_drained.get("speaker_responses", [])
        if speaker == "scout"
    ][0]
    rebuked_scout_line = [
        text
        for speaker, text in rebuked_drained.get("speaker_responses", [])
        if speaker == "scout"
    ][0]

    assert sided_scout_line != rebuked_scout_line
    assert "没拖后腿" in sided_scout_line
    assert "别误会" in rebuked_scout_line
