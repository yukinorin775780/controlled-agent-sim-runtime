"""
NPC 赛博义体：Function Calling (Tools) 定义
供大模型通过原生工具调用实现物理自治与防诈骗能力
"""

from core.tools.npc_tools import check_target_inventory, execute_physical_action

__all__ = ["check_target_inventory", "execute_physical_action"]
