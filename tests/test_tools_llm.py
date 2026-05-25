"""
隔离测试：验证 Agent 循环可连续执行工具调用。
不依赖 LangChain，也不加载任何真实模型。
"""

import json
from unittest.mock import Mock

from tests.llm_test_doubles import FakeLLMResponse, FakeMessage, FakeToolMessage


def test_npc_agent_loop():
    player_inventory = {"治疗药水": 1, "金币": 100}
    analyst_inventory = {"未知协议的圣徽": 1}

    def check_target_inventory(item_keyword: str, target_id: str) -> str:
        inventory = (
            player_inventory if target_id == "player" else analyst_inventory
        )
        has_item = item_keyword in inventory and inventory[item_keyword] > 0
        return json.dumps(
            {"has_item": has_item, "target_id": target_id, "item": item_keyword}
        )

    def transfer_item(item_keyword: str, source_id: str, target_id: str) -> str:
        source_inv = player_inventory if source_id == "player" else analyst_inventory
        target_inv = player_inventory if target_id == "player" else analyst_inventory

        if item_keyword in source_inv and source_inv[item_keyword] > 0:
            source_inv[item_keyword] -= 1
            target_inv[item_keyword] = target_inv.get(item_keyword, 0) + 1
            return json.dumps({"status": "success"})
        return json.dumps({"status": "failed"})

    llm_with_tools = Mock()
    llm_with_tools.invoke.side_effect = [
        FakeLLMResponse(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "check_target_inventory",
                    "args": {"item_keyword": "治疗药水", "target_id": "player"},
                }
            ]
        ),
        FakeLLMResponse(
            tool_calls=[
                {
                    "id": "call-2",
                    "name": "transfer_item",
                    "args": {
                        "item_keyword": "治疗药水",
                        "source_id": "player",
                        "target_id": "analyst",
                    },
                }
            ]
        ),
        FakeLLMResponse(content="哼，至少这次你真的把药水拿出来了。"),
    ]

    messages = [
        FakeMessage(content="system prompt"),
        FakeMessage(content="分析员，我把刚才那瓶治疗药水递给你。"),
    ]

    while True:
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            if tool_call["name"] == "check_target_inventory":
                result = check_target_inventory(**tool_call["args"])
            elif tool_call["name"] == "transfer_item":
                result = transfer_item(**tool_call["args"])
            else:
                result = json.dumps({"error": "tool not found"})
            messages.append(
                FakeToolMessage(content=result, tool_call_id=tool_call["id"])
            )

    assert response.content == "哼，至少这次你真的把药水拿出来了。"
    assert player_inventory["治疗药水"] == 0
    assert analyst_inventory["治疗药水"] == 1
    assert llm_with_tools.invoke.call_count == 3
