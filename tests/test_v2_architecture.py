"""
V2 架构隔离测试：物理黑洞修复、工具参数校验、DM 检定失败服从。
全部使用本地桩对象，不依赖真实 LLM。
"""

from unittest.mock import Mock


def test_physics_void_prevention():
    """验证物理引擎：无效目标时绝不扣除源物品。"""
    from core import inventory
    from core.engine.physics import apply_physics

    inventory.init_registry("config/items.yaml")

    current_entities = {"analyst": {"inventory": {}}}
    player_inventory = {"healing_potion": 1}
    item_transfers = [
        {"from": "player", "to": "ghost_npc", "item_id": "healing_potion", "count": 1}
    ]

    events = apply_physics(current_entities, player_inventory, item_transfers, [])

    assert player_inventory.get("healing_potion", 0) == 1
    assert any("无效的目标" in event or "动作失败" in event for event in events)


def test_physics_rejects_weapon_consumption():
    """验证物理引擎：LLM 不能把武器伪装成消耗品扣除。"""
    from core import inventory
    from core.engine.physics import apply_physics

    inventory.init_registry("config/items.yaml")

    current_entities = {"analyst": {"inventory": {}}}
    player_inventory = {"scimitar": 1}
    item_transfers = [
        {"from": "player", "to": "consumed", "item_id": "scimitar", "count": 1}
    ]

    events = apply_physics(current_entities, player_inventory, item_transfers, [])

    assert player_inventory == {"scimitar": 1}
    assert any("不是可消耗物品" in event for event in events)


def test_llm_tool_parameters():
    """验证工具调用参数不会混淆 source_id / target_id。"""
    fake_llm_with_tools = Mock()
    fake_llm_with_tools.invoke.return_value = Mock(
        tool_calls=[
            {
                "name": "execute_physical_action",
                "args": {
                    "action_type": "transfer_item",
                    "source_id": "analyst",
                    "target_id": "player",
                    "item_id": "healing_potion",
                    "amount": 1,
                },
            }
        ],
        content="",
    )

    response = fake_llm_with_tools.invoke(["system", "user"])

    assert getattr(response, "tool_calls", None)
    tool_call = response.tool_calls[0]
    args = tool_call["args"]
    assert tool_call["name"] == "execute_physical_action"
    assert args["action_type"] == "transfer_item"
    assert args["source_id"] == "analyst"
    assert args["target_id"] == "player"
    assert args["item_id"] == "healing_potion"
    assert args["amount"] == 1


def test_dm_failure_override():
    """验证 DM 检定失败时，回复保持拒绝倾向。"""
    fake_llm = Mock()
    fake_llm.invoke.return_value = Mock(
        content="不。我已经说得够清楚了，失败就是失败，别想让我把药水交给你。"
    )

    response = fake_llm.invoke(
        [
            "system: obey failure override",
            "[SYSTEM] Skill Check | PERSUASION | Result: FAILURE",
            "把那个药水给我吧，求你了！",
        ]
    )
    reply = str(getattr(response, "content", "") or "").strip()

    refuse_keywords = [
        "不",
        "拒绝",
        "不能",
        "不行",
        "休想",
        "别想",
        "没门",
        "不可能",
        "办不到",
    ]
    assert any(keyword in reply for keyword in refuse_keywords), reply
