"""
单元测试：文本处理器 (core.utils.text_processor)
覆盖 clean_npc_dialogue、format_history_message、parse_llm_json。
"""

import logging
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，支持直接运行 python tests/test_text_processor.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.utils.text_processor import (
    clean_npc_dialogue,
    format_history_message,
    parse_llm_json,
)


# -------------------------------------------------------------------------
# clean_npc_dialogue
# -------------------------------------------------------------------------


def test_clean_npc_dialogue():
    speaker = "Analyst"

    test_cases = [
        ("[Analyst]说： 别碰那个哨子。", "别碰那个哨子。"),
        ("： [Analyst]说： 风停了。", "风停了。"),
        ("Analyst: 你听见脚步声了吗？", "你听见脚步声了吗？"),
        ("*她叹了口气* 我们走吧。", "*她叹了口气* 我们走吧。"),
        ("我觉得不太对劲。", "我觉得不太对劲。"),
    ]

    for raw_text, expected in test_cases:
        result = clean_npc_dialogue(speaker, raw_text)
        assert result == expected, (
            f"清洗失败！\n原文本: {raw_text}\n期望: {expected}\n实际: {result}"
        )


# -------------------------------------------------------------------------
# format_history_message
# -------------------------------------------------------------------------


def test_format_history_message():
    speaker = "Scout"
    clean_text = "哎哟，你是在教我做事吗？"
    expected = "[Scout]: 哎哟，你是在教我做事吗？"

    assert format_history_message(speaker, clean_text) == expected


# -------------------------------------------------------------------------
# parse_llm_json
# -------------------------------------------------------------------------


def test_parse_llm_json_plain():
    """纯 JSON 应正常解析"""
    raw = '{"action_type": "CHAT", "difficulty_class": 0}'
    result = parse_llm_json(raw)
    assert result == {"action_type": "CHAT", "difficulty_class": 0}


def test_parse_llm_json_markdown_wrapped():
    """Markdown 代码块包裹的 JSON 应自动剥离"""
    raw = '```json\n{"action_type": "ATTACK", "dc": 15}\n```'
    result = parse_llm_json(raw)
    assert result == {"action_type": "ATTACK", "dc": 15}


def test_parse_llm_json_markdown_json_prefix():
    """带 json 语言标识的代码块"""
    raw = '```json\n{"key": "value"}\n```'
    result = parse_llm_json(raw)
    assert result == {"key": "value"}


def test_parse_llm_json_extracts_json_from_surrounding_text():
    """前后带说明文字时，仍应提取出 JSON 主体"""
    raw = '分析如下：\n```json\n{"action_type": "ATTACK", "dc": 18}\n```\n请执行。'
    result = parse_llm_json(raw)
    assert result == {"action_type": "ATTACK", "dc": 18}


def test_parse_llm_json_prefers_first_valid_fenced_json_block():
    """多个代码块并存时，应优先使用第一个合法 JSON 代码块"""
    raw = (
        "先看示例：\n"
        "```text\nnot json\n```\n"
        "最终结果：\n"
        "```json\n{\"reply\": \"准备好了\", \"state_changes\": {\"affection_delta\": 2}}\n```"
    )
    result = parse_llm_json(raw)
    assert result == {
        "reply": "准备好了",
        "state_changes": {"affection_delta": 2},
    }


def test_parse_llm_json_falls_back_to_embedded_json_object():
    """没有代码块时，应能从普通文本中提取嵌入的 JSON 对象"""
    raw = '模型回复：{"action_type":"CHAT","difficulty_class":0,"reason":"safe"}，请继续。'
    result = parse_llm_json(raw)
    assert result == {
        "action_type": "CHAT",
        "difficulty_class": 0,
        "reason": "safe",
    }


def test_parse_llm_json_sanitizes_positive_number_prefixes():
    """应清洗对象、嵌套对象与数组中的非法正号前缀"""
    raw = """
    ```json
    {
      "difficulty_class": +15,
      "state_changes": {"affection_delta": +2.5},
      "roll_modifiers": [+1, 0, -2]
    }
    ```
    """
    result = parse_llm_json(raw)
    assert result == {
        "difficulty_class": 15,
        "state_changes": {"affection_delta": 2.5},
        "roll_modifiers": [1, 0, -2],
    }


def test_parse_llm_json_invalid_returns_empty(caplog):
    """无效 JSON 应返回空字典并记录警告"""
    with caplog.at_level(logging.WARNING):
        result = parse_llm_json("not json at all")

    assert result == {}
    assert "无效的 JSON 格式" in caplog.text
    assert "not json at all" in caplog.text


def test_parse_llm_json_invalid_fenced_json_returns_empty(caplog):
    """损坏的 JSON 代码块应回退为空字典并记录警告"""
    raw = '```json\n{"action_type": "CHAT",}\n```'

    with caplog.at_level(logging.WARNING):
        result = parse_llm_json(raw)

    assert result == {}
    assert "无效的 JSON 格式" in caplog.text
    assert '{"action_type": "CHAT",}' in caplog.text


def test_parse_llm_json_empty_string():
    """空字符串应返回空字典"""
    result = parse_llm_json("")
    assert result == {}


def test_parse_llm_json_whitespace_only():
    """仅空白字符应返回空字典"""
    result = parse_llm_json("   \n\t  ")
    assert result == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
