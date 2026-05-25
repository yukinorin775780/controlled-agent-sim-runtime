import asyncio
from unittest.mock import patch

from core.graph.nodes.dialogue import dialogue_node
from core.graph.nodes.dm import dm_node
from core.graph.nodes.event_drain import event_drain_node
from core.campaigns.hazard_lab import (
    detect_diary_negotiation_context,
    detect_key_guidance_context,
    detect_study_chest_loot_context,
)
from core.systems import mechanics
from core.systems.maps import load_maps
from core.systems.world_init import get_initial_world_state


def _build_lab_state() -> dict:
    load_maps(force_reload=True)
    state = get_initial_world_state(map_id="hazard_lab")
    entities = state.get("entities") or {}
    # 统一修正敌对阵营标记，避免 "enemy" 与 "hostile" 的旧卡数据差异影响测试稳定性
    for entity_id in ("drone_guard_1", "drone_guard_2"):
        entity = entities.get(entity_id)
        if isinstance(entity, dict):
            entity["faction"] = "hostile"
    state["demo_cleared"] = False
    return state


def test_hazard_lab_kill_loot_then_interact_clears_demo():
    state = _build_lab_state()
    entities = state["entities"]
    entities["player"]["x"] = 17
    entities["player"]["y"] = 4
    gatekeeper = entities["gatekeeper"]
    gatekeeper["hp"] = 0
    gatekeeper["status"] = "dead"
    gatekeeper["inventory"] = {"heavy_iron_key": 1}

    loot_result = mechanics.execute_loot_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "gatekeeper",
            },
        }
    )

    if loot_result.get("pending_events"):
        drained = event_drain_node({**state, **loot_result})
        loot_state = {**state, **loot_result, **drained}
    else:
        loot_state = loot_result

    assert loot_state["player_inventory"].get("heavy_iron_key", 0) == 1
    assert loot_state["entities"]["gatekeeper"]["inventory"] == {}
    assert any("搜刮" in event for event in loot_result.get("journal_events", []))

    interact_result = mechanics.execute_interact_action(
        {
            **state,
            **{
                **loot_state,
                "entities": {
                    **(loot_state.get("entities") or {}),
                    "player": {
                        **((loot_state.get("entities") or {}).get("player") or {}),
                        "x": 17,
                        "y": 4,
                    },
                },
            },
            "intent_context": {
                "action_actor": "player",
                "action_target": "heavy_oak_door_1",
            },
        }
    )

    door = interact_result["entities"]["heavy_oak_door_1"]
    assert door["is_open"] is True
    assert interact_result.get("demo_cleared") is True
    assert any("DEMO CLEARED" in event for event in interact_result.get("journal_events", []))


def test_hazard_lab_interact_without_key_fails():
    state = _build_lab_state()
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4

    interact_result = mechanics.execute_interact_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "heavy_oak_door_1",
            },
        }
    )

    door = interact_result["entities"]["heavy_oak_door_1"]
    assert door["is_open"] is False
    assert interact_result.get("demo_cleared", False) is False
    assert any("需要一把沉重的铁钥匙" in event for event in interact_result.get("journal_events", []))


def test_hazard_lab_dialogue_transfer_key_then_interact_clears_demo():
    state = _build_lab_state()
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4
    state["entities"]["gatekeeper"]["faction"] = "neutral"
    state["entities"]["gatekeeper"]["inventory"] = {"heavy_iron_key": 1}

    start_result = dialogue_node(
        {
            **state,
            "intent": "START_DIALOGUE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "gatekeeper",
            },
            "user_input": "我想和守门人谈谈",
        }
    )
    assert start_result.get("active_dialogue_target") == "gatekeeper"

    mocked_dialogue_json = (
        '{"internal_monologue":"",'
        '"reply":"拿去吧，别烦我。",'
        '"trigger_combat": false,'
        '"state_changes":{"patience_delta":0,"fear_delta":0},'
        '"physical_action":{"action_type":"transfer_item","source_id":"gatekeeper","target_id":"player","item_id":"heavy_iron_key","count":1}}'
    )

    with patch("core.engine.generate_dialogue", return_value=mocked_dialogue_json):
        dialogue_result = dialogue_node(
            {
                **state,
                **start_result,
                "intent": "DIALOGUE_REPLY",
                "intent_context": {
                    "action_actor": "player",
                    "action_target": "gatekeeper",
                },
                "user_input": "把钥匙给我",
            }
        )

    assert dialogue_result["pending_events"]
    tx_event = dialogue_result["pending_events"][0]
    assert tx_event["event_type"] == "actor_item_transaction_requested"
    assert tx_event["payload"]["transaction"]["transaction_type"] == "transfer"
    assert tx_event["payload"]["transaction"]["from_entity"] == "gatekeeper"
    assert tx_event["payload"]["transaction"]["to_entity"] == "player"
    assert tx_event["payload"]["transaction"]["item"] == "heavy_iron_key"
    assert dialogue_result["player_inventory"].get("heavy_iron_key", 0) == 0
    assert dialogue_result["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 1

    drained_patch = event_drain_node(
        {
            **state,
            **start_result,
            **dialogue_result,
        }
    )
    drained_state = {
        **state,
        **start_result,
        **dialogue_result,
        **drained_patch,
    }
    assert drained_state["player_inventory"].get("heavy_iron_key", 0) == 1
    assert drained_state["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 0
    assert any(
        "heavy_iron_key" in event or "沉重铁钥匙" in event
        for event in drained_state.get("journal_events", [])
    )

    interact_result = mechanics.execute_interact_action(
        {
            **drained_state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "heavy_oak_door_1",
            },
        }
    )

    assert interact_result["entities"]["heavy_oak_door_1"]["is_open"] is True
    assert interact_result.get("demo_cleared") is True
    assert any("DEMO CLEARED" in event for event in interact_result.get("journal_events", []))


def test_hazard_lab_act4_loot_key_uses_event_drain_and_prevents_duplication():
    state = _build_lab_state()
    state["flags"]["world_hazard_lab_gatekeeper_defeated"] = True
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 9
    state["entities"]["gatekeeper"]["x"] = 4
    state["entities"]["gatekeeper"]["y"] = 9
    state["entities"]["gatekeeper"]["hp"] = 18
    state["entities"]["gatekeeper"]["status"] = "alive"
    state["entities"]["gatekeeper"]["inventory"] = {"heavy_iron_key": 1}

    loot_result = mechanics.execute_loot_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "gatekeeper",
            },
        }
    )

    assert loot_result["pending_events"]
    tx_event = loot_result["pending_events"][0]
    assert tx_event["event_type"] == "actor_item_transaction_requested"
    assert tx_event["payload"]["transaction"]["transaction_type"] == "transfer"
    assert tx_event["payload"]["transaction"]["item"] == "heavy_iron_key"
    assert loot_result["player_inventory"].get("heavy_iron_key", 0) == 0
    assert loot_result["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 1

    drained_patch = event_drain_node(
        {
            **state,
            **loot_result,
        }
    )
    drained_state = {
        **state,
        **loot_result,
        **drained_patch,
    }
    assert drained_state["pending_events"] == []
    assert drained_state["player_inventory"].get("heavy_iron_key", 0) == 1
    assert drained_state["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 0

    second_loot_result = mechanics.execute_loot_action(
        {
            **drained_state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "gatekeeper",
            },
        }
    )
    assert second_loot_result.get("pending_events", []) == []
    assert second_loot_result["player_inventory"].get("heavy_iron_key", 0) == 1


def test_hazard_lab_open_door_sets_escape_completion_flags():
    state = _build_lab_state()
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4
    state["player_inventory"]["heavy_iron_key"] = 1
    state["flags"] = {}

    interact_result = mechanics.execute_interact_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "heavy_oak_door_1",
            },
        }
    )

    assert interact_result["entities"]["heavy_oak_door_1"]["is_open"] is True
    assert interact_result.get("demo_cleared") is True
    assert interact_result["flags"]["hazard_lab_escape_complete"] is True
    assert interact_result["flags"]["content_sprint_1_complete"] is True


def test_final_exit_uses_tiled_aligned_coordinates_and_alias():
    state = _build_lab_state()
    state["entities"]["player"]["x"] = 17
    state["entities"]["player"]["y"] = 4
    state["player_inventory"]["heavy_iron_key"] = 1

    for target in ("heavy_oak_door_1", "exit_door"):
        interact_result = mechanics.execute_interact_action(
            {
                **state,
                "intent_context": {
                    "action_actor": "player",
                    "action_target": target,
                },
            }
        )

        raw_result = interact_result["raw_roll_data"]["result"]
        assert raw_result["result_type"] == "SUCCESS"
        assert raw_result["is_success"] is True
        assert interact_result["entities"]["heavy_oak_door_1"]["is_open"] is True
        assert interact_result["flags"]["act4_final_exit_opened"] is True
        assert interact_result["demo_cleared"] is True


def test_key_guidance_context_returns_none_outside_hazard_lab():
    state = get_initial_world_state(map_id="training_range")

    result = detect_key_guidance_context(state, "怎么打开实验室门？", "scout")

    assert result is None


def test_key_guidance_context_missing_lab_key():
    state = _build_lab_state()
    state["player_inventory"].pop("lab_key", None)
    state["player_inventory"].pop("heavy_iron_key", None)

    result = detect_key_guidance_context(state, "怎么打开实验室门？", "scout")

    assert result is not None
    assert result["topic"] == "lab_key"
    assert result["door_id"] == "door_b_to_d"
    assert result["has_lab_key"] is False
    assert "study_chest" in result["missing_key_hint"]


def test_key_guidance_context_has_lab_key():
    state = _build_lab_state()
    state["player_inventory"]["lab_key"] = 1

    result = detect_key_guidance_context(state, "现在能打开实验室门了吗？", "analyst")

    assert result is not None
    assert result["has_lab_key"] is True
    assert "door_b_to_d" in result["has_key_hint"]


def test_key_guidance_context_suppressed_after_door_open():
    state = _build_lab_state()
    state["entities"]["door_b_to_d"] = {
        "entity_type": "door",
        "is_open": True,
        "status": "open",
    }

    result = detect_key_guidance_context(state, "怎么打开实验室门？", "tactician")

    assert result is None


def test_diary_negotiation_context_returns_none_outside_hazard_lab():
    state = get_initial_world_state(map_id="training_range")
    state["active_dialogue_target"] = "gatekeeper"

    result = detect_diary_negotiation_context(state, "日记里写了你喝下危害狂暴灵药。")

    assert result is None


def test_diary_negotiation_context_without_decoded_diary_reports_no_evidence():
    state = _build_lab_state()
    state["active_dialogue_target"] = "gatekeeper"
    state["flags"].pop("hazard_lab_diary_decoded", None)
    state["flags"].pop("hazard_lab_antidote_formula_fragment_known", None)
    state["flags"].pop("hazard_lab_key_hint_known", None)

    result = detect_diary_negotiation_context(state, "我知道你喝了什么药，把钥匙给我。")

    assert result is not None
    assert result["topic"] == "gatekeeper_elixir_truth"
    assert result["decoded_diary"] is False
    assert result["evidence"] == []


def test_diary_negotiation_context_decoded_diary_returns_pressure_evidence():
    state = _build_lab_state()
    state["active_dialogue_target"] = "gatekeeper"
    state["flags"]["hazard_lab_diary_decoded"] = True
    state["flags"]["hazard_lab_antidote_formula_fragment_known"] = True
    state["flags"]["hazard_lab_key_hint_known"] = True

    result = detect_diary_negotiation_context(
        state,
        "日记里写了你喝下危害狂暴灵药，钥匙和解药线索都和这件事有关。",
    )

    assert result is not None
    assert result["decoded_diary"] is True
    assert result["target_actor_id"] == "gatekeeper"
    assert "hazard_diary" in result["evidence"]
    assert "antidote_fragment" in result["evidence"]
    assert "key_hint" in result["evidence"]


def test_study_chest_loot_context_detects_open_or_loot_text():
    state = _build_lab_state()

    result = detect_study_chest_loot_context(state, "打开 chest_1")
    alias_result = detect_study_chest_loot_context(state, "搜刮书房箱子")

    assert result is not None
    assert result["target_id"] == "chest_1"
    assert result["item_id"] == "lab_key"
    assert alias_result is not None
    assert alias_result["target_id"] == "chest_1"


def test_study_chest_alias_loot_grants_lab_key():
    state = _build_lab_state()

    result = mechanics.execute_loot_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "study_chest",
            },
        }
    )

    assert result["player_inventory"].get("lab_key") == 1
    assert result["environment_objects"]["chest_1"]["inventory"].get("lab_key", 0) == 0
    assert "heavy_iron_key" not in result["player_inventory"]
    assert any("lab_key x 1" in line for line in result.get("journal_events", []))


def test_chest_1_loot_grants_lab_key_and_repeat_does_not_duplicate():
    state = _build_lab_state()

    first = mechanics.execute_loot_action(
        {
            **state,
            "intent_context": {
                "action_actor": "player",
                "action_target": "chest_1",
            },
        }
    )
    second = mechanics.execute_loot_action(
        {
            **state,
            **first,
            "intent_context": {
                "action_actor": "player",
                "action_target": "chest_1",
            },
        }
    )

    assert first["player_inventory"].get("lab_key") == 1
    assert second["player_inventory"].get("lab_key") == 1
    assert second["environment_objects"]["chest_1"]["inventory"] == {}


def test_text_open_chest_1_routes_to_loot():
    state = _build_lab_state()
    state.update(
        {
            "user_input": "打开 chest_1",
            "intent": "chat",
            "speaker_responses": [],
            "pending_events": [],
        }
    )

    result = asyncio.run(dm_node(state))

    assert result["intent"] == "LOOT"
    assert result["intent_context"]["reason"] == "hazard_lab_study_chest_loot"
    assert result["intent_context"]["action_target"] == "chest_1"
