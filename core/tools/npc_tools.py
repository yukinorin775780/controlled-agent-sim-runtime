"""
NPC 赛博义体 (Tools)：使用 LangChain @tool 定义 Schema
实际执行逻辑在 Graph Node 中结合 GameState 拦截并执行
"""

from langchain_core.tools import tool


@tool
def check_target_inventory(target_id: str, item_keyword: str) -> str:
    """
    核实目标角色的背包中是否包含某物。当玩家声称要给你某物时，必须先调用此工具验证目标（通常是 player）包里到底有没有该物品。

    target_id: 目标的ID，通常为 "player"（要核实的是玩家的背包）。
    item_keyword: 物品的名称或关键词，例如 "药水"、"金币"。
    """
    return ""  # 实际逻辑将在 Graph Node 中结合 GameState 拦截并执行


@tool
def execute_physical_action(
    action_type: str,
    source_id: str = "",
    target_id: str = "",
    item_id: str = "",
    amount: int = 1,
) -> str:
    """
    在核实无误后，执行实质性的物理动作。

    action_type: 只能是 'transfer_item' (转移物品), 'heal' (治疗), 'damage' (伤害)。
    source_id: 物品转移时，失去物品的角色ID。治疗/伤害时可为空。
    target_id: 物品转移时，获得物品的角色ID；治疗/伤害时，承受效果的目标ID。
    item_id: 物品ID（仅 transfer_item 时使用）。
    amount: 数量或血量变动值。

    🚨 CRITICAL PERSPECTIVE RULES FOR transfer_item:
    - If the player gives YOU (the NPC) an item: source_id MUST be 'player', target_id MUST be your character ID (e.g., 'analyst').
    - If YOU (the NPC) give the player an item: source_id MUST be your character ID, target_id MUST be 'player'.
    DO NOT mix up source and target!
    """
    return ""  # 同样在 Graph Node 中拦截执行
