"""
测试专用的轻量 LLM / Message 替身，避免导入真实 LangChain 与底层 ML 依赖。
"""

from dataclasses import dataclass, field
from typing import Any, List, Sequence


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeToolMessage(FakeMessage):
    tool_call_id: str


@dataclass
class FakeLLMResponse:
    content: str = ""
    tool_calls: List[dict[str, Any]] = field(default_factory=list)


class SequencedInvoker:
    """按预设顺序返回响应，并记录每次 invoke 的 messages。"""

    def __init__(self, responses: Sequence[FakeLLMResponse]):
        self._responses = list(responses)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> FakeLLMResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("测试桩没有剩余的 LLM 响应可返回。")
        return self._responses.pop(0)
