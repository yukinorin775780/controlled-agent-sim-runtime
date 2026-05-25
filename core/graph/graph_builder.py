"""
LangGraph 应用工厂。
构建并编译 simulation Agent 图，支持条件路由与 Checkpointer 持久化。
Checkpointer 由调用方（如 main.py）创建并传入，支持 AsyncSqliteSaver 等异步实现。
"""

from langgraph.graph import END, START, StateGraph

from core.graph.graph_routers import (
    route_after_actor_invocation,
    route_after_dm,
    route_after_mechanics,
    route_after_narration,
)
from core.graph.graph_state import GameState
from core.graph.nodes.actor_invocation import actor_invocation_node
from core.graph.nodes.dm import advance_speaker_node, dm_node, narration_node
from core.graph.nodes.dialogue import dialogue_node
from core.graph.nodes.event_drain import event_drain_node
from core.graph.nodes.generation import create_generation_node
from core.graph.nodes.input import input_node, world_tick_node
from core.graph.nodes.lore_node import lore_node
from core.graph.nodes.mechanics import mechanics_node


def route_after_input(state: dict) -> str:
    """拦截开发者指令与纯系统指令（不进入 world_tick / DM，不唤醒 LLM）"""
    intent = state.get("intent", "")
    if intent in ("dev_command", "command_failed", "command_done"):
        return "__end__"
    return "world_tick"


def route_after_tick(state: dict) -> str:
    """只短路 system_wait；聊天和 action_use 进入 DM 判定和大模型反应"""
    if state.get("intent") == "system_wait":
        return "__end__"
    return "dm_analysis"


def route_after_generation(state: dict) -> str:
    """多人发言队列：若 speaker_queue 非空，继续让下一位发言"""
    if state.get("speaker_queue"):
        return "advance_speaker"
    return "__end__"


# -----------------------------------------------------------------------------
# Checkpointer 与 GameState 协同说明
# -----------------------------------------------------------------------------
#
# LangGraph 的 Checkpointer 会在每个「超级步」（super-step）结束时，
# 将当前 State 序列化并写入 SQLite。我们的 GameState 包含：
#   - messages: 对话历史（add_messages 累加）
#   - relationship, npc_state, player_inventory, npc_inventory, flags
#   - journal_events（merge_events 累加）
#
# 通过 thread_id 区分不同存档：
#   - config = {"configurable": {"thread_id": "analyst_save_1"}}
#   - 同一 thread_id 的 invoke 会从上次 checkpoint 恢复 messages 等状态
#   - 不同 thread_id 则开启全新会话
#
# 注意：在 V2 架构中，SqliteSaver (Checkpointer) 作为唯一的 Single Source of Truth，
# 统一接管了 messages、relationship、inventory 等所有业务状态的跨会话持久化。
# 彻底废弃了原有的 JSON 文件读写方案。
# -----------------------------------------------------------------------------


# --- Graph Builder ---


def build_graph(checkpointer=None):
    """
    构建并编译 LangGraph 应用。
    使用传入的 Checkpointer 启用持久化与 thread_id 会话隔离。
    若未传入 checkpointer，则编译为无持久化图。
    """
    builder = StateGraph(GameState)

    # 1. Add Nodes
    builder.add_node("input_processing", input_node)
    builder.add_node("world_tick", world_tick_node)  # type: ignore[arg-type]
    builder.add_node("dm_analysis", dm_node)
    builder.add_node("dialogue_processing", dialogue_node)
    builder.add_node("lore_processing", lore_node)
    builder.add_node("mechanics_processing", mechanics_node)
    builder.add_node("actor_invocation", actor_invocation_node)  # type: ignore[arg-type]
    builder.add_node("event_drain", event_drain_node)
    builder.add_node("narration", narration_node)
    builder.add_node("generation", create_generation_node())  # type: ignore[arg-type]
    builder.add_node("advance_speaker", advance_speaker_node)

    # 2. Add Edges & Routing
    builder.add_edge(START, "input_processing")
    builder.add_conditional_edges(
        "input_processing",
        route_after_input,
        {"__end__": END, "world_tick": "world_tick"},
    )
    builder.add_conditional_edges(
        "world_tick",
        route_after_tick,
        {"dm_analysis": "dm_analysis", "__end__": END},
    )
    builder.add_conditional_edges(
        "dm_analysis",
        route_after_dm,
        {
            "mechanics_processing": "mechanics_processing",
            "dialogue_processing": "dialogue_processing",
            "lore_processing": "lore_processing",
            "generation": "actor_invocation",
        },
    )
    builder.add_edge("dialogue_processing", END)
    builder.add_edge("lore_processing", END)
    builder.add_conditional_edges(
        "mechanics_processing",
        route_after_mechanics,
        {"generation": "actor_invocation", "narration": "narration"},
    )
    # DM 旁白后：30% 概率触发吐槽，大成功/大失败 100% 吐槽
    builder.add_conditional_edges(
        "narration",
        route_after_narration,
        {"generation": "actor_invocation", "__end__": END},
    )
    builder.add_conditional_edges(
        "actor_invocation",
        route_after_actor_invocation,
        {"event_drain": "event_drain", "generation": "generation", "__end__": END},
    )
    builder.add_conditional_edges(
        "event_drain",
        route_after_generation,
        {"advance_speaker": "advance_speaker", "__end__": END},
    )
    builder.add_conditional_edges(
        "generation",
        route_after_generation,
        {"advance_speaker": "advance_speaker", "__end__": END},
    )
    builder.add_edge("advance_speaker", "generation")

    # 3. Compile
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()


__all__ = ["build_graph"]
