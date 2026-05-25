import asyncio
from unittest.mock import Mock, patch

from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.actors.registry import get_default_actor_registry
from core.systems import mechanics
from core.systems.maps import load_maps
from core.systems.world_init import get_initial_world_state


class _FakeRetriever:
    def retrieve_for_actor(self, *args, **kwargs):
        return []

    def retrieve_for_director(self, *args, **kwargs):
        return []


def _run_guidance_turn(state: dict, user_input: str) -> dict:
    turn_state = {
        **state,
        "user_input": user_input,
        "intent": "chat",
        "speaker_responses": [],
        "pending_events": [],
    }
    dm_patch = asyncio.run(dm_node(turn_state))
    patched_state = {**turn_state, **dm_patch}

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


def _response_text(state: dict) -> str:
    return " ".join(text for _, text in (state.get("speaker_responses") or []))


def test_actor_runtime_missing_key_guidance_contains_key_study_and_lockpick_tokens():
    state = get_initial_world_state(map_id="hazard_lab")
    state["player_inventory"].pop("lab_key", None)
    state["player_inventory"].pop("heavy_iron_key", None)

    result = _run_guidance_turn(state, "怎么打开实验室门？")
    text = _response_text(result)

    assert result["actor_invocation_mode"] == "runtime"
    assert "lab_key" in text or "钥匙" in text
    assert "书房" in text
    assert "暗门" in text or "hazard_diary" in text
    assert "撬锁" in text


def test_actor_runtime_has_key_guidance_points_to_open_lab_door():
    state = get_initial_world_state(map_id="hazard_lab")
    state["player_inventory"]["lab_key"] = 1

    result = _run_guidance_turn(state, "现在能打开实验室门了吗？")
    text = _response_text(result)

    assert "钥匙" in text or "lab_key" in text
    assert "打开" in text
    assert "实验室门" in text or "door_b_to_d" in text
    assert "去找钥匙" not in text


def test_missing_key_guidance_does_not_modify_inventory():
    state = get_initial_world_state(map_id="hazard_lab")
    state["player_inventory"].pop("lab_key", None)

    result = _run_guidance_turn(state, "钥匙在哪？")

    assert "lab_key" not in result["player_inventory"]


def test_has_key_guidance_does_not_consume_lab_key():
    state = get_initial_world_state(map_id="hazard_lab")
    state["player_inventory"]["lab_key"] = 1

    result = _run_guidance_turn(state, "现在能打开实验室门了吗？")

    assert result["player_inventory"]["lab_key"] == 1


def test_key_guidance_writes_visible_journal_marker():
    state = get_initial_world_state(map_id="hazard_lab")

    result = _run_guidance_turn(state, "这扇实验室门怎么办？")

    assert any("[队友建议]" in line and "topic=lab_key" in line for line in result.get("journal_events", []))


def test_looted_study_chest_lab_key_triggers_has_key_guidance():
    load_maps(force_reload=True)
    state = get_initial_world_state(map_id="hazard_lab")
    loot_result = mechanics.execute_loot_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "study_chest",
            },
        }
    )

    result = _run_guidance_turn({**state, **loot_result}, "现在能打开实验室门了吗？")
    text = _response_text(result)

    assert result["player_inventory"].get("lab_key") == 1
    assert "打开" in text
    assert "实验室门" in text or "door_b_to_d" in text
    assert "去找钥匙" not in text
