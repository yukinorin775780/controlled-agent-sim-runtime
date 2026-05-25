"""
动态 Persona 状态机单元测试。
仅验证状态流转和 JSON 解析，不加载任何真实 AI 模型。
"""

from unittest.mock import Mock

from core.utils.text_processor import parse_llm_json
from tests.llm_test_doubles import FakeLLMResponse, FakeMessage


def test_persona_state_machine_applies_deltas_and_clamps_bounds():
    """多轮对话应按 JSON delta 更新状态，并钳制到 0-100 区间。"""
    fake_llm = Mock()
    fake_llm.invoke.side_effect = [
        FakeLLMResponse(
            content="""
            ```json
            {
              "reply": "这护符让我不舒服。",
              "internal_monologue": "这个符号让我想起一些模糊的东西。",
              "state_changes": {
                "affection_delta": 10,
                "protocol_confidence_delta": -15,
                "memory_awakening_delta": 20
              }
            }
            ```
            """
        ),
        FakeLLMResponse(
            content='{"reply":"别试图替我定义我的协议立场。","internal_monologue":"她的话刺中了我。","state_changes":{"affection_delta":5,"protocol_confidence_delta":-40,"memory_awakening_delta":30}}'
        ),
        FakeLLMResponse(
            content='分析如下：{"reply":"……我知道，你不是在利用我。","internal_monologue":"也许我真的可以相信她。","state_changes":{"affection_delta":50,"protocol_confidence_delta":-60,"memory_awakening_delta":80}}'
        ),
    ]

    fake_character = Mock()
    fake_character.render_prompt.side_effect = [
        "prompt-round-1",
        "prompt-round-2",
        "prompt-round-3",
    ]

    state = {
        "affection": 50,
        "protocol_confidence": 90,
        "memory_awakening": 10,
    }
    test_queries = [
        "我在废墟里捡到了一块刻着维护标记的数据卡，你看看眼熟吗？",
        "你没发现未知协议的安全规则只是在利用你的痛苦吗？",
        "不管你遵循哪套协议，我都会保护你的后背。",
    ]

    for query in test_queries:
        system_prompt = fake_character.render_prompt(
            relationship_score=state["affection"],
            protocol_confidence=state["protocol_confidence"],
            memory_awakening=state["memory_awakening"],
        )
        response = fake_llm.invoke(
            [FakeMessage(content=system_prompt), FakeMessage(content=query)]
        )
        parsed = parse_llm_json(response.content)
        changes = parsed["state_changes"]

        state["affection"] = max(
            0,
            min(100, state["affection"] + changes.get("affection_delta", 0)),
        )
        state["protocol_confidence"] = max(
            0,
            min(100, state["protocol_confidence"] + changes.get("protocol_confidence_delta", 0)),
        )
        state["memory_awakening"] = max(
            0,
            min(
                100,
                state["memory_awakening"]
                + changes.get("memory_awakening_delta", 0),
            ),
        )

    assert state == {
        "affection": 100,
        "protocol_confidence": 0,
        "memory_awakening": 100,
    }
    assert fake_character.render_prompt.call_args_list[0].kwargs == {
        "relationship_score": 50,
        "protocol_confidence": 90,
        "memory_awakening": 10,
    }
    assert fake_character.render_prompt.call_args_list[1].kwargs == {
        "relationship_score": 60,
        "protocol_confidence": 75,
        "memory_awakening": 30,
    }
    assert fake_character.render_prompt.call_args_list[2].kwargs == {
        "relationship_score": 65,
        "protocol_confidence": 35,
        "memory_awakening": 60,
    }
    assert fake_llm.invoke.call_count == 3
