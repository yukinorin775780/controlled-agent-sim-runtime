"""LangGraph 图引擎层：状态、节点、路由与构建。"""

from core.graph.graph_state import GameState, merge_events


def build_graph(*args, **kwargs):
    """惰性导入图构建器，避免仅导入状态类型时拉起重依赖。"""
    from core.graph.graph_builder import build_graph as _build_graph

    return _build_graph(*args, **kwargs)

__all__ = [
    "GameState",
    "merge_events",
    "build_graph",
]
