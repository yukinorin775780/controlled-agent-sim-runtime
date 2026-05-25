from unittest.mock import Mock

from core.llm import dm


def test_analyze_intent_routes_hazard_door_open_phrases_to_interact(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for door heuristics"))
    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    available_targets = ["player", "gatekeeper", "heavy_oak_door_1", "chest_1"]
    commands = [
        "打开门",
        "开门",
        "使用钥匙打开门",
        "用 heavy_iron_key 打开门",
        "检查 heavy_oak_door_1",
    ]
    for command in commands:
        result = dm.analyze_intent(
            command,
            available_npcs=["player", "scout", "analyst"],
            available_targets=available_targets,
        )
        assert result["action_type"] == "INTERACT"
        assert result["action_target"] == "heavy_oak_door_1"


def test_analyze_intent_keeps_explicit_attack_door_as_attack(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for door attack heuristics"))
    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "攻击门",
        available_npcs=["player", "scout"],
        available_targets=["player", "heavy_oak_door_1"],
    )
    assert result["action_type"] == "ATTACK"
    assert result["action_target"] == "heavy_oak_door_1"
