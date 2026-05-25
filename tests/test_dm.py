"""
DM 模块保护性测试。
锁定安全规则解析、叙事规则覆写和延迟初始化行为。
"""

import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import openai
import pytest

import core.llm.dm as dm


def test_evaluate_rule_condition_supports_numeric_boolean_and_membership():
    context = {
        "affection": 65,
        "hp": 4,
        "has_healing_potion": True,
        "action_type": "ATTACK",
        "is_probing_secret": True,
        "flags": {
            "secret_revealed": True,
            "abandoned_protocol": False,
            "knows_secret": True,
        },
    }

    assert dm._evaluate_rule_condition("affection >= 50", context) is True
    assert dm._evaluate_rule_condition("hp < 5", context) is True
    assert dm._evaluate_rule_condition("has_healing_potion == True", context) is True
    assert (
        dm._evaluate_rule_condition(
            "action_type in ['INTIMIDATION', 'ATTACK']",
            context,
        )
        is True
    )
    assert (
        dm._evaluate_rule_condition(
            "flags.get('secret_revealed', False) and is_probing_secret",
            context,
        )
        is True
    )
    assert dm._evaluate_rule_condition("flags.knows_secret == True", context) is True


def test_evaluate_rule_condition_rejects_unsafe_expression(caplog):
    with caplog.at_level("WARNING"):
        result = dm._evaluate_rule_condition("__import__('os').system('pwd')", {})

    assert result is False
    assert "Unsupported rule expression" in caplog.text


def test_evaluate_narrative_rules_applies_matching_overrides():
    fake_character = SimpleNamespace(
        data={
            "narrative_rules": [
                {
                    "id": "secret_probe",
                    "condition": "flags.get('secret_revealed', False) and is_probing_secret",
                    "overrides": {
                        "difficulty_class": 18,
                        "reason": "Secret probing should be resisted.",
                    },
                },
                {
                    "id": "abandoned_protocol_attack",
                    "condition": (
                        "flags.get('abandoned_protocol', False) and "
                        "(is_probing_secret or action_type in ['INTIMIDATION', 'ATTACK'])"
                    ),
                    "overrides": {"action_type": "CHAT"},
                },
            ]
        }
    )

    analysis = {
        "action_type": "ATTACK",
        "difficulty_class": 10,
        "reason": "Initial decision.",
        "is_probing_secret": True,
    }
    flags = {"secret_revealed": True, "abandoned_protocol": False}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(dm, "load_character", Mock(return_value=fake_character))
        result = dm._evaluate_narrative_rules(analysis, flags, target_npc="analyst")

    assert result["difficulty_class"] == 18
    assert result["reason"] == "Secret probing should be resisted."
    assert result["action_type"] == "ATTACK"


def test_evaluate_narrative_rules_player_target_no_exception():
    analysis = {"action_type": "CHAT", "reason": "noop"}
    with pytest.MonkeyPatch.context() as monkeypatch:
        mocked_loader = Mock(side_effect=AssertionError("player must not be loaded"))
        monkeypatch.setattr(dm, "load_character", mocked_loader)
        result = dm._evaluate_narrative_rules(dict(analysis), {}, target_npc="player")
    assert result == analysis
    mocked_loader.assert_not_called()


def test_evaluate_narrative_rules_missing_yaml_no_exception():
    analysis = {"action_type": "CHAT", "reason": "noop"}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(dm, "load_character", Mock(side_effect=FileNotFoundError("missing")))
        result = dm._evaluate_narrative_rules(dict(analysis), {}, target_npc="unknown_xxx")
    assert result == analysis


def test_importing_dm_does_not_initialize_openai_client_without_api_key(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("OpenAI client must not initialize at import"))

    monkeypatch.setattr(openai, "OpenAI", sentinel)
    monkeypatch.setattr(dm.settings, "API_KEY", None)

    reloaded = importlib.reload(dm)

    assert sentinel.call_count == 0
    assert reloaded.parse_json_response('{"ok": true}') == {"ok": True}


def test_analyze_intent_requires_api_key_only_at_runtime(monkeypatch):
    monkeypatch.setattr(dm.settings, "API_KEY", None)
    monkeypatch.setattr(
        dm,
        "load_dm_template",
        Mock(return_value=SimpleNamespace(render=Mock(return_value="prompt"))),
    )

    with pytest.raises(RuntimeError, match="API Key"):
        dm.analyze_intent("你好")


def test_analyze_intent_detects_loot_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for loot heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我要搜刮 drone_1",
        available_npcs=["analyst", "drone_1"],
    )

    assert result["action_type"] == "LOOT"
    assert result["difficulty_class"] == 0
    assert result["action_actor"] == "player"
    assert result["action_target"] == "drone_1"
    assert result["responders"] == ["analyst"]


def test_analyze_intent_detects_move_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for move heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我走向训练无人机",
        available_npcs=["analyst", "drone_1"],
    )

    assert result["action_type"] == "MOVE"
    assert result["difficulty_class"] == 0
    assert result["action_actor"] == "player"
    assert result["action_target"] == "drone_1"
    assert result["responders"] == ["analyst"]


def test_analyze_intent_detects_read_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for read heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "玩家阅读实验日志",
        available_npcs=["analyst", "scout", "player"],
        available_targets=["player", "journal_1", "drone_guard_1"],
    )

    assert result["action_type"] == "READ"
    assert result["difficulty_class"] == 0
    assert result["action_actor"] == "player"
    assert result["action_target"] == "journal_1"


def test_analyze_intent_detects_diary_reading_phrase_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for read heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "看看书上写了什么",
        available_npcs=["analyst", "scout", "player"],
        available_targets=["player", "hazard_diary", "drone_guard_1"],
    )

    assert result["action_type"] == "READ"
    assert result["difficulty_class"] == 0
    assert result["action_actor"] == "player"
    assert result["action_target"] == "hazard_diary"


def test_analyze_intent_extracts_commanded_actor_and_player_target_for_move(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for move heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "让分析员走向我",
        available_npcs=["analyst", "scout"],
        available_targets=["player", "analyst", "camp_fire"],
    )

    assert result["action_type"] == "MOVE"
    assert result["action_actor"] == "analyst"
    assert result["action_target"] == "player"
    assert result["responders"] == ["analyst"]


def test_analyze_intent_extracts_camp_fire_alias_for_move(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for move heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "分析员，去篝火",
        available_npcs=["analyst", "scout"],
        available_targets=["player", "analyst", "camp_fire"],
    )

    assert result["action_type"] == "MOVE"
    assert result["action_actor"] == "analyst"
    assert result["action_target"] == "camp_fire"


def test_analyze_intent_detects_delegated_move_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for delegated move heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "分析员，走到我身边来！",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "analyst", "camp_fire"],
    )

    assert result["action_type"] == "MOVE"
    assert result["action_actor"] == "analyst"
    assert result["action_target"] == "player"
    assert result["responders"] == ["analyst"]


def test_analyze_intent_detects_delegated_attack_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for delegated attack heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "战术员，砍死那只训练无人机！",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1", "tactician"],
    )

    assert result["action_type"] == "ATTACK"
    assert result["action_actor"] == "tactician"
    assert result["action_target"] == "drone_1"
    assert result["responders"] == ["tactician"]


def test_analyze_intent_detects_shove_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for shove heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "玩家把训练无人机推开",
        available_npcs=["analyst", "scout", "player"],
        available_targets=["player", "drone_1", "iron_chest"],
    )

    assert result["action_type"] == "SHOVE"
    assert result["action_actor"] == "player"
    assert result["action_target"] == "drone_1"


def test_analyze_intent_detects_delegated_loot_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for delegated loot heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "让战术员搜刮箱子",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "iron_chest", "tactician"],
    )

    assert result["action_type"] == "LOOT"
    assert result["action_actor"] == "tactician"
    assert result["action_target"] == "iron_chest"
    assert result["responders"] == ["tactician"]


def test_analyze_intent_prioritizes_unlock_over_move_keyword(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for unlock heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "侦察员，去把宝箱撬开！",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "scout", "iron_chest"],
    )

    assert result["action_type"] == "UNLOCK"
    assert result["difficulty_class"] == 14
    assert result["action_actor"] == "scout"
    assert result["action_target"] == "iron_chest"
    assert result["responders"] == ["scout"]


def test_analyze_intent_detects_disarm_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for disarm heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "侦察员解除绊线陷阱",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "trap_tripwire_1", "iron_chest"],
    )

    assert result["action_type"] == "DISARM"
    assert result["difficulty_class"] == 15
    assert result["action_actor"] == "scout"
    assert result["action_target"] == "trap_tripwire_1"
    assert result["responders"] == ["scout"]


def test_analyze_intent_detects_equip_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for equip heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我装备上弯刀",
        available_npcs=["analyst", "scout"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "EQUIP"
    assert result["difficulty_class"] == 0
    assert result["action_actor"] == "player"
    assert result["action_target"] == "scimitar"
    assert result["item_id"] == "scimitar"


def test_analyze_intent_detects_unequip_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for unequip heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我卸下弯刀",
        available_npcs=["analyst", "scout"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "UNEQUIP"
    assert result["action_actor"] == "player"
    assert result["action_target"] == "scimitar"


def test_analyze_intent_detects_use_item_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for use-item heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "侦察员喝下治疗药水",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "USE_ITEM"
    assert result["action_actor"] == "scout"
    assert result["action_target"] == "healing_potion"
    assert result["item_id"] == "healing_potion"


def test_analyze_intent_detects_stealth_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for stealth heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "侦察员进入潜行",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "STEALTH"
    assert result["action_actor"] == "scout"
    assert result["action_target"] == ""


def test_analyze_intent_detects_start_dialogue_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for start-dialogue heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我想和守门人聊聊",
        available_npcs=["analyst", "scout", "gatekeeper"],
        available_targets=["player", "gatekeeper", "drone_1"],
    )

    assert result["action_type"] == "START_DIALOGUE"
    assert result["action_actor"] == "player"
    assert result["action_target"] == "gatekeeper"
    assert result["responders"] == ["gatekeeper"]


def test_analyze_intent_forces_dialogue_reply_when_session_is_locked(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for dialogue lock"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "你刚刚说什么？",
        available_npcs=["analyst", "scout", "gatekeeper"],
        available_targets=["player", "gatekeeper", "drone_1"],
        active_dialogue_target="gatekeeper",
    )

    assert result["action_type"] == "DIALOGUE_REPLY"
    assert result["action_actor"] == "player"
    assert result["action_target"] == "gatekeeper"


def test_analyze_intent_breaks_dialogue_lock_on_physical_attack(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for physical-attack break"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我拔剑砍他",
        available_npcs=["analyst", "scout", "gatekeeper"],
        available_targets=["player", "gatekeeper", "drone_1"],
        active_dialogue_target="gatekeeper",
    )

    assert result["action_type"] == "ATTACK"
    assert result["action_target"] == "gatekeeper"
    assert result["clear_active_dialogue_target"] is True


def test_analyze_intent_detects_short_rest_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for short-rest heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我们短休一下",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "SHORT_REST"
    assert result["action_actor"] == "player"
    assert result["action_target"] == ""


def test_analyze_intent_detects_long_rest_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for long-rest heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "我们扎营长休",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1"],
    )

    assert result["action_type"] == "LONG_REST"
    assert result["action_actor"] == "player"
    assert result["action_target"] == ""


def test_analyze_intent_detects_cast_spell_command_without_calling_llm(monkeypatch):
    sentinel = Mock(side_effect=AssertionError("LLM should not be called for cast spell heuristic"))

    monkeypatch.setattr(dm, "_get_openai_client", sentinel)

    result = dm.analyze_intent(
        "Analyst使用雷鸣波",
        available_npcs=["analyst", "scout", "tactician"],
        available_targets=["player", "drone_1", "iron_chest"],
    )

    assert result["action_type"] == "CAST_SPELL"
    assert result["action_actor"] == "analyst"
    assert result["action_target"] == "drone_1"
    assert result["spell_id"] == "thunderwave"
    assert result["responders"] == ["analyst"]


def test_analyze_intent_uses_lazy_client_and_normalizes_response(monkeypatch):
    fake_completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"action_type":"attack","difficulty_class":"12",'
                        '"reason":"hostile","is_probing_secret":true,'
                        '"responders":["SCOUT","unknown"],'
                        '"action_target":"Drone_1",'
                        '"flags_changed":{"met_secret":1},'
                        '"item_transfers":[{"from":"player","to":"scout","item_id":"healing_potion","count":2}],'
                        '"hp_changes":[{"target":"player","amount":-3}],'
                        '"affection_changes":{"analyst":3,"scout":2}}'
                    )
                )
            )
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=fake_completion))
        )
    )

    monkeypatch.setattr(dm.settings, "API_KEY", "test-key")
    monkeypatch.setattr(
        dm,
        "load_dm_template",
        Mock(return_value=SimpleNamespace(render=Mock(return_value="prompt"))),
    )
    monkeypatch.setattr(dm, "_get_openai_client", Mock(return_value=fake_client))
    monkeypatch.setattr(
        dm,
        "load_character",
        Mock(return_value=SimpleNamespace(data={"narrative_rules": []})),
    )

    result = dm.analyze_intent(
        "动手吧",
        flags={"known": True},
        available_npcs=["analyst", "scout"],
    )

    assert result["action_type"] == "ATTACK"
    assert result["difficulty_class"] == 12
    assert result["is_probing_secret"] is True
    assert result["responders"] == ["scout"]
    assert result["action_target"] == "drone_1"
    assert result["flags_changed"] == {"met_secret": True}
    assert result["item_transfers"] == [
        {
            "from": "player",
            "to": "scout",
            "item_id": "healing_potion",
            "count": 2,
        }
    ]
    assert result["hp_changes"] == [{"target": "player", "amount": -3}]
    assert result["affection_changes"] == {"scout": 2}


def test_analyze_intent_no_npc_target_skips_player_character_load(monkeypatch):
    fake_completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"action_type":"CHAT","difficulty_class":0,"reason":"ok","responders":["player"],"action_actor":"player","action_target":""}'
                )
            )
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=fake_completion))
        )
    )
    observed_load_targets: list[str] = []

    def _fake_load_character(name: str):
        observed_load_targets.append(str(name))
        return SimpleNamespace(data={"narrative_rules": []})

    monkeypatch.setattr(dm.settings, "API_KEY", "test-key")
    monkeypatch.setattr(
        dm,
        "load_dm_template",
        Mock(return_value=SimpleNamespace(render=Mock(return_value="prompt"))),
    )
    monkeypatch.setattr(dm, "_get_openai_client", Mock(return_value=fake_client))
    monkeypatch.setattr(dm, "load_character", Mock(side_effect=_fake_load_character))

    result = dm.analyze_intent(
        "随便聊聊",
        available_npcs=["player", "analyst"],
        available_targets=["player", "camp_fire"],
    )

    assert result["responders"] == ["analyst"]
    assert result["action_actor"] == "player"
    assert result["action_target"] == ""
    assert "player" not in [target.lower() for target in observed_load_targets]
