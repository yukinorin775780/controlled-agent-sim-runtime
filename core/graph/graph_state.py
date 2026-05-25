"""
LangGraph state definition for the Controlled Agent Agent.
GameState is the shared memory (baton) passed between graph nodes.

Uses operator and Annotated reducers for enhanced state merge semantics:
- messages: add_messages (LangGraph standard for conversation flow)
- journal_events: merge_events (accumulate event lists across nodes)
"""

import operator
from typing import TypedDict, Annotated, List, Dict, Any, Tuple, Optional
from langgraph.graph.message import add_messages


def merge_events(left: List[str], right: List[str]) -> List[str]:
    """
    Reducer: Merge journal event lists by concatenation.
    When multiple nodes append events (e.g. InputNode + MechanicsNode),
    the final state accumulates all events in order.
    """
    return operator.add(left or [], right or [])


class GameState(TypedDict, total=False):
    """
    The central state object for the Controlled Agent Agent Graph.
    This 'baton' is passed between all nodes (Input -> DM -> Mechanics -> Generation).

    Field categories:
    -----------------
    [PERSISTENT - 持久化存档数据]
    Survive across turns; saved/loaded by MemoryManager.

    [TRANSIENT - 单轮瞬时上下文]
    Scoped to one invoke; derived from or produced within the turn.
    """

    # -------------------------------------------------------------------------
    # Conversation History
    # LangGraph standard: add_messages handles append/dedupe for chat flow.
    # [PERSISTENT] Persisted as history in saves.
    # -------------------------------------------------------------------------
    messages: Annotated[List[Any], add_messages]

    # -------------------------------------------------------------------------
    # Input Processing [TRANSIENT]
    # -------------------------------------------------------------------------
    user_input: str         # Raw player input this turn
    target: str             # Optional structured action/dialogue target from API/UI
    source: str             # Optional structured source channel or actor hint from API/UI
    speaker_queue: List[str]  # 需要发言的 NPC 队列，例如 ["scout", "analyst"]
    current_speaker: str     # 当前正在生成的 NPC
    intent: str             # DM-analyzed 机制动作 (e.g. "ATTACK", "PERSUASION", "CHAT")
    intent_context: Dict[str, Any]  # DM 输出的 difficulty_class、reason 等
    is_probing_secret: bool  # 话题标签：是否在刺探未知协议立场/神器等核心隐私（意图 How 与话题 What 分离）
    active_dialogue_target: Optional[str]  # 当前会话锁定的交涉目标 entity_id
    demo_cleared: bool  # Demo 关卡是否已通关

    # -------------------------------------------------------------------------
    # simulation State [PERSISTENT]
    # -------------------------------------------------------------------------
    character_name: str
    npc_state: Dict[str, Any]  # e.g. {"status": "SILENT", "duration": 2}

    # -------------------------------------------------------------------------
    # Inventories [PERSISTENT]
    # Dict[str, int]: item_id -> quantity
    # -------------------------------------------------------------------------
    player_inventory: Dict[str, int]
    npc_inventory: Dict[str, int]

    # -------------------------------------------------------------------------
    # Quest & World [PERSISTENT]
    # -------------------------------------------------------------------------
    flags: Dict[str, bool]
    turn_count: int         # 世界心跳：当前回合数
    time_of_day: str        # 世界心跳：当前时段 (晨曦/正午/黄昏/深夜)
    entities: Dict[str, Dict[str, Any]]  # 多角色实体状态 {entity_id: {hp, active_buffs}, ...}
    current_location: str   # 当前所处的场景名称
    environment_objects: Dict[str, Dict[str, Any]]  # 环境中的可交互物体 (如宝箱、门)
    map_data: Dict[str, Any]  # 当前战斗地图尺寸与障碍数据
    combat_phase: str
    combat_active: bool
    initiative_order: List[str]
    current_turn_index: int
    turn_resources: Dict[str, Dict[str, Any]]
    recent_barks: List[Dict[str, Any]]
    pending_events: List[Dict[str, Any]]
    reflection_queue: List[Dict[str, Any]]
    actor_runtime_state: Dict[str, Dict[str, Any]]
    last_actor_decision: Dict[str, Any]
    actor_invocation_mode: str
    actor_invocation_reason: str

    # -------------------------------------------------------------------------
    # Journal Events [TRANSIENT within turn]
    # merge_events reducer: nodes append events; final state = accumulated list.
    # Consumed by GenerationNode for context; flushed per turn by main.py.
    # -------------------------------------------------------------------------
    journal_events: Annotated[List[str], merge_events]

    # -------------------------------------------------------------------------
    # UI Animation Data [TRANSIENT]
    # -------------------------------------------------------------------------
    latest_roll: Dict[str, Any]  # 存储最近一次掷骰子的明细，供 UI 拦截并播放动画

    # -------------------------------------------------------------------------
    # Output to Renderer [TRANSIENT]
    # -------------------------------------------------------------------------
    final_response: str      # Spoken dialogue to display（单人时用；多人时为最后一位）
    speaker_responses: List[Tuple[str, str]]  # 多人发言队列产出：[(speaker_id, text), ...]
    thought_process: str     # Inner monologue content
