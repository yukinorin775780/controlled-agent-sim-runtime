from core.graph.nodes.lore import lore_node
from core.actors.visibility import filter_flags_for_actor
from typing import Optional


def _build_read_state(int_score: int) -> dict:
    return {
        "intent": "READ",
        "intent_context": {
            "action_actor": "player",
            "action_target": "journal_1",
        },
        "entities": {
            "player": {
                "name": "玩家",
                "ability_scores": {
                    "STR": 10,
                    "DEX": 10,
                    "CON": 10,
                    "INT": int_score,
                    "WIS": 10,
                    "CHA": 10,
                },
                "hp": 20,
                "max_hp": 20,
                "status": "alive",
            }
        },
        "environment_objects": {
            "journal_1": {
                "id": "journal_1",
                "type": "readable",
                "name": "残破的实验日志",
                "lore_id": "hazard_journal_1",
                "x": 6,
                "y": 10,
            }
        },
    }


def test_lore_node_with_low_int_obscures_password(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_read_state(8)

    result = lore_node(state)
    text = "\n".join(result.get("journal_events", []))
    action_segment = text.split("\n\n", 1)[1] if "\n\n" in text else text

    assert "📜 [原文]" in text
    assert "📖 [动作]" in text
    assert "💬 [独白]" in text
    assert "控制阀口令" not in action_segment
    assert "Control Valve Phrase" not in action_segment
    assert ("看懂" in text) or ("混乱" in text) or ("只言片语" in text)


def test_lore_node_with_high_int_reveals_password(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_read_state(16)

    result = lore_node(state)
    text = "\n".join(result.get("journal_events", []))

    assert "📜 [原文]" in text
    assert "📖 [动作]" in text
    assert "💬 [独白]" in text
    assert "控制阀口令" in text
    assert "Control Valve Phrase" in text


def test_lore_node_diary_generates_narration_and_monologue(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = {
        "intent": "READ",
        "intent_context": {
            "action_actor": "scout",
            "action_target": "hazard_diary",
        },
        "entities": {
            "scout": {
                "name": "侦察员 (Scout)",
                "ability_scores": {"INT": 16},
                "attributes": {
                    "personality": {
                        "traits": ["傲慢", "毒舌", "精于观察"]
                    }
                },
                "hp": 15,
                "max_hp": 15,
                "status": "alive",
            }
        },
        "environment_objects": {
            "hazard_diary": {
                "id": "hazard_diary",
                "type": "readable",
                "name": "沾满血污的日记本",
                "lore_id": "hazard_diary_1",
                "x": 15,
                "y": 3,
            }
        },
    }

    result = lore_node(state)
    text = "\n".join(result.get("journal_events", []))
    assert "📜 [原文]" in text
    assert "📖 [动作]" in text
    assert "Gatekeeper" in text
    assert "铁钥匙" in text
    assert "💬 [独白]" in text


def _build_diary_read_state(
    *,
    int_score: int,
    actor_id: str = "player",
    skill: Optional[str] = None,
    user_input: str = "阅读 hazard_diary",
) -> dict:
    intent_context = {
        "action_actor": actor_id,
        "action_target": "hazard_diary",
    }
    if skill:
        intent_context["skill"] = skill
    return {
        "intent": "READ",
        "user_input": user_input,
        "current_location": "hazard_lab",
        "intent_context": intent_context,
        "entities": {
            "player": {
                "name": "玩家",
                "ability_scores": {"INT": int_score},
                "hp": 20,
                "max_hp": 20,
                "status": "alive",
            },
            "scout": {
                "name": "侦察员",
                "ability_scores": {"INT": 13},
                "hp": 15,
                "max_hp": 15,
                "status": "alive",
            },
        },
        "environment_objects": {
            "hazard_diary": {
                "id": "hazard_diary",
                "type": "readable",
                "name": "沾满血污的日记本",
                "lore_id": "hazard_diary_1",
                "x": 15,
                "y": 3,
            }
        },
    }


def test_lore_node_diary_success_sets_flags_and_memory_events(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_diary_read_state(int_score=16, skill="arcana")

    result = lore_node(state)
    text = "\n".join(result.get("journal_events", []))
    flags = result.get("flags") or {}
    pending_events = result.get("pending_events") or []

    assert flags["hazard_lab_diary_read"] is True
    assert flags["hazard_lab_diary_decoded"] is True
    assert flags["act3_diary_read"] is True
    assert flags["act3_diary_decoded"] is True
    assert flags["act3_gatekeeper_potion_truth_known"] is True
    assert flags["act3_party_knows_gatekeeper_truth"] is True
    assert flags["hazard_lab_key_hint_known"]["visibility"]["scope"] == "party"
    assert flags["hazard_lab_antidote_formula_fragment_known"]["visibility"]["scope"] == "actor"

    assert "Gatekeeper" in text
    assert "毒气陷阱" in text
    assert "heavy_iron_key" in text
    assert "解药配方其实就在" in text
    assert len(pending_events) == 2
    assert pending_events[0]["event_type"] == "actor_memory_update_requested"
    assert pending_events[0]["payload"]["scope"] == "actor_private"
    assert pending_events[0]["actor_id"] == "player"
    assert pending_events[1]["payload"]["scope"] == "party_shared"


def test_lore_node_diary_failure_sets_decoded_false_without_full_knowledge(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_diary_read_state(int_score=8, user_input="阅读这本血污日记")

    result = lore_node(state)
    text = "\n".join(result.get("journal_events", []))
    flags = result.get("flags") or {}
    pending_events = result.get("pending_events") or []

    assert flags["hazard_lab_diary_read"] is True
    assert flags["hazard_lab_diary_decoded"] is False
    assert flags["act3_diary_read"] is True
    assert flags["act3_diary_decoded"] is False
    assert flags["act3_gatekeeper_potion_truth_known"] is False
    assert "hazard_lab_key_hint_known" not in flags
    assert "解药配方其实就在" not in text
    assert "heavy_iron_key" not in text
    assert ("零碎" in text) or ("碎片" in text)
    assert len(pending_events) == 1
    assert pending_events[0]["payload"]["scope"] == "actor_private"
    assert "Gatekeeper" not in pending_events[0]["payload"]["text"]


def test_lore_node_diary_arcana_and_investigation_mapping(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    monkeypatch.setattr("core.systems.dice.random.randint", lambda _a, _b: 12)

    arcana_state = _build_diary_read_state(int_score=10, skill="arcana", user_input="用奥术知识读日记")
    arcana_result = lore_node(arcana_state)
    assert arcana_result["flags"]["hazard_lab_diary_decoded"] is True
    assert arcana_result["latest_roll"]["skill"] == "arcana"
    assert arcana_result["latest_roll"]["result"]["is_success"] is True

    investigation_state = _build_diary_read_state(
        int_score=10,
        skill="investigation",
        user_input="我调查这本日记",
    )
    investigation_result = lore_node(investigation_state)
    assert investigation_result["flags"]["hazard_lab_diary_decoded"] is False
    assert investigation_result["latest_roll"]["skill"] == "investigation"
    assert investigation_result["latest_roll"]["result"]["is_success"] is False


def test_lore_node_diary_browser_equivalent_payload_returns_latest_roll_and_memory(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_diary_read_state(
        int_score=16,
        skill="arcana",
        user_input="",
    )

    result = lore_node(state)
    latest_roll = result.get("latest_roll") or {}
    pending_events = result.get("pending_events") or []

    assert latest_roll.get("intent") == "READ"
    assert latest_roll.get("target") == "hazard_diary"
    assert latest_roll.get("result", {}).get("is_success") is True
    assert any(event.get("event_type") == "actor_memory_update_requested" for event in pending_events)


def test_lore_node_diary_actor_scoped_clue_not_visible_to_other_actor(monkeypatch):
    monkeypatch.setattr("core.graph.nodes.lore.settings.API_KEY", None)
    state = _build_diary_read_state(int_score=16, skill="arcana")
    result = lore_node(state)

    merged_state = {**state, **result}
    player_flags = filter_flags_for_actor(result.get("flags") or {}, "player", state=merged_state)
    scout_flags = filter_flags_for_actor(result.get("flags") or {}, "scout", state=merged_state)

    assert player_flags["hazard_lab_antidote_formula_fragment_known"] is True
    assert "hazard_lab_antidote_formula_fragment_known" not in scout_flags
    assert scout_flags["hazard_lab_key_hint_known"] is True
