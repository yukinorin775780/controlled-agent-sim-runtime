"""
Dialogue Node 测试：会话锁与谈判破裂开战。
"""

from core.graph.nodes.dialogue import dialogue_node
from core.graph.nodes.event_drain import event_drain_node


def _build_minimal_dialogue_state() -> dict:
    return {
        "intent": "START_DIALOGUE",
        "intent_context": {"action_actor": "player", "action_target": "gatekeeper"},
        "user_input": "和守门人搭话",
        "time_of_day": "晨曦 (Morning)",
        "messages": [],
        "entities": {
            "player": {
                "name": "玩家",
                "faction": "player",
                "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
                "hp": 20,
                "max_hp": 20,
                "status": "alive",
                "inventory": {},
            },
            "gatekeeper": {
                "name": "Gatekeeper",
                "faction": "neutral",
                "ability_scores": {"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 10, "CHA": 8},
                "hp": 18,
                "max_hp": 18,
                "status": "alive",
                "inventory": {"heavy_iron_key": 1},
                "dynamic_states": {
                    "patience": {"current_value": 3},
                    "fear": {"current_value": 5},
                },
            },
        },
    }


def test_start_dialogue_sets_active_target_and_emits_system_hint():
    state = _build_minimal_dialogue_state()

    result = dialogue_node(state)

    assert result["active_dialogue_target"] == "gatekeeper"
    assert "准备交涉" in result["journal_events"][0]


def test_dialogue_reply_decrements_patience_and_breaks_into_combat(monkeypatch):
    responses = iter(
        [
            '{"internal_monologue":"", "reply":"别浪费我的时间。", "trigger_combat": false, "state_changes": {"patience_delta": -1, "fear_delta": 0}}',
            '{"internal_monologue":"", "reply":"够了！", "trigger_combat": false, "state_changes": {"patience_delta": -2, "fear_delta": 0}}',
        ]
    )

    def _fake_generate_dialogue(system_prompt: str, conversation_history=None):
        return next(responses)

    monkeypatch.setattr("core.engine.generate_dialogue", _fake_generate_dialogue)

    state = _build_minimal_dialogue_state()
    state["intent"] = "DIALOGUE_REPLY"
    state["active_dialogue_target"] = "gatekeeper"
    state["user_input"] = "你到底行不行"

    first = dialogue_node(state)
    assert first["active_dialogue_target"] == "gatekeeper"
    assert first["entities"]["gatekeeper"]["dynamic_states"]["patience"]["current_value"] == 2
    assert not first.get("combat_active", False)

    next_state = {
        **state,
        **first,
        "intent": "DIALOGUE_REPLY",
        "intent_context": {"action_actor": "player", "action_target": "gatekeeper"},
        "user_input": "你就是个笑话",
    }
    second = dialogue_node(next_state)

    assert second["active_dialogue_target"] is None
    assert second["combat_phase"] == "IN_COMBAT"
    assert second["combat_active"] is True
    assert "gatekeeper" in second["initiative_order"]
    assert "谈判破裂" in "\n".join(second["journal_events"])


def test_dialogue_transfer_item_emits_pending_event_without_direct_inventory_mutation(monkeypatch):
    def _fake_generate_dialogue(system_prompt: str, conversation_history=None):
        _ = (system_prompt, conversation_history)
        return (
            '{"internal_monologue":"",'
            '"reply":"拿去吧，别烦我。",'
            '"trigger_combat": false,'
            '"state_changes":{"patience_delta":0,"fear_delta":0},'
            '"physical_action":{"action_type":"transfer_item","source_id":"gatekeeper","target_id":"player","item_id":"heavy_iron_key","count":1}}'
        )

    monkeypatch.setattr("core.engine.generate_dialogue", _fake_generate_dialogue)

    state = _build_minimal_dialogue_state()
    state["intent"] = "DIALOGUE_REPLY"
    state["active_dialogue_target"] = "gatekeeper"
    state["player_inventory"] = {}
    state["user_input"] = "把钥匙给我"

    result = dialogue_node(state)

    assert result["player_inventory"] == {}
    assert result["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 1
    assert result["pending_events"]
    event = result["pending_events"][0]
    assert event["event_type"] == "actor_item_transaction_requested"
    transaction = event["payload"]["transaction"]
    assert transaction["transaction_type"] == "transfer"
    assert transaction["from_entity"] == "gatekeeper"
    assert transaction["to_entity"] == "player"
    assert transaction["item"] == "heavy_iron_key"
    assert transaction["accepted"] is True
    assert result["speaker_responses"] == [("gatekeeper", "拿去吧，别烦我。")]

    drained = event_drain_node({**state, **result})
    assert drained["player_inventory"]["heavy_iron_key"] == 1
    assert drained["entities"]["gatekeeper"]["inventory"].get("heavy_iron_key", 0) == 0
