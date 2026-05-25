"""
LangGraph 节点包：按职责拆分的 Input / DM / Mechanics / Generation。
"""

from importlib import import_module

_EXPORTS = {
    "input_node": ("core.graph.nodes.input", "input_node"),
    "world_tick_node": ("core.graph.nodes.input", "world_tick_node"),
    "dm_node": ("core.graph.nodes.dm", "dm_node"),
    "advance_speaker_node": ("core.graph.nodes.dm", "advance_speaker_node"),
    "narration_node": ("core.graph.nodes.dm", "narration_node"),
    "dialogue_node": ("core.graph.nodes.dialogue", "dialogue_node"),
    "lore_node": ("core.graph.nodes.lore_node", "lore_node"),
    "mechanics_node": ("core.graph.nodes.mechanics", "mechanics_node"),
    "create_generation_node": ("core.graph.nodes.generation", "create_generation_node"),
    "generation_node": ("core.graph.nodes.generation", "generation_node"),
}


def __getattr__(name):
    """惰性导出节点函数，避免包导入时触发 generation / LLM 依赖。"""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)

__all__ = [
    "input_node",
    "world_tick_node",
    "dm_node",
    "advance_speaker_node",
    "narration_node",
    "dialogue_node",
    "lore_node",
    "mechanics_node",
    "create_generation_node",
    "generation_node",
]
