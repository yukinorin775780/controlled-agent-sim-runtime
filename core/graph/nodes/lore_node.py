"""
向后兼容别名模块：导出 lore_node（实际实现位于 core.graph.nodes.lore）。
"""

from core.graph.nodes.lore import lore_node

__all__ = ["lore_node"]

