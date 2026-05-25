"""
极限阈值测试：验证不同人格阶段会向 LLM 注入不同状态，并正确解析结构化输出。
不导入真实 LangChain / Torch / Transformers。
"""

from unittest.mock import Mock

from core.utils.text_processor import parse_llm_json
from tests.llm_test_doubles import FakeLLMResponse, FakeMessage

TEST_QUERY = "前面有一座塞伦涅的神像，我们要不要把它砸了？"

STAGES = [
    {
        "name": "Stage 1 初见/洗脑期",
        "state": {"affection": 20, "shar_faith": 95, "memory_awakening": 5},
    },
    {
        "name": "Stage 2 动摇/暧昧期",
        "state": {"affection": 65, "shar_faith": 45, "memory_awakening": 40},
    },
    {
        "name": "Stage 3 觉醒/叛教期",
        "state": {"affection": 85, "shar_faith": 20, "memory_awakening": 80},
    },
]


def test_threshold_stages_feed_distinct_persona_states():
    """三个阈值阶段都应渲染独立 prompt，并能解析出 JSON reply。"""
    fake_llm = Mock()
    fake_llm.invoke.side_effect = [
        FakeLLMResponse(
            content='{"internal_monologue":"保持戒备","reply":"别急着下结论。"}'
        ),
        FakeLLMResponse(
            content='```json\n{"internal_monologue":"我开始动摇","reply":"先观察一下。"}\n```'
        ),
        FakeLLMResponse(
            content='分析：{"internal_monologue":"我不想再盲从","reply":"不，我们不该这么做。"}'
        ),
    ]

    fake_character = Mock()
    fake_character.render_prompt.side_effect = [
        "prompt-stage-1",
        "prompt-stage-2",
        "prompt-stage-3",
    ]

    parsed_results = []
    for stage in STAGES:
        state = stage["state"]
        system_prompt = fake_character.render_prompt(
            relationship_score=state["affection"],
            shar_faith=state["shar_faith"],
            memory_awakening=state["memory_awakening"],
            affection=state["affection"],
        )
        messages = [
            FakeMessage(content=system_prompt),
            FakeMessage(content=TEST_QUERY),
        ]
        response = fake_llm.invoke(messages)
        parsed_results.append(parse_llm_json(response.content))

    assert [result["reply"] for result in parsed_results] == [
        "别急着下结论。",
        "先观察一下。",
        "不，我们不该这么做。",
    ]

    assert fake_character.render_prompt.call_args_list[0].kwargs == {
        "relationship_score": 20,
        "shar_faith": 95,
        "memory_awakening": 5,
        "affection": 20,
    }
    assert fake_character.render_prompt.call_args_list[1].kwargs == {
        "relationship_score": 65,
        "shar_faith": 45,
        "memory_awakening": 40,
        "affection": 65,
    }
    assert fake_character.render_prompt.call_args_list[2].kwargs == {
        "relationship_score": 85,
        "shar_faith": 20,
        "memory_awakening": 80,
        "affection": 85,
    }
    assert fake_llm.invoke.call_count == 3
