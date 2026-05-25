"""
应用服务层：封装聊天回合的状态恢复、Genesis、图编排与响应整形。
"""

import copy
import inspect
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import START

from core.campaigns import detect_lab_intro_awareness
from core.actors.reflection import run_reflection_tick
from core.engine.physics import execute_loot
from core.eval.telemetry import emit_telemetry
from core.graph.graph_builder import build_graph
from core.graph.nodes.event_drain import event_drain_node
from core.memory.compat import get_default_memory_service
from core.memory.models import TurnMemoryInput
from core.memory.service import MemoryService
from core.systems import mechanics
from core.systems.world_init import get_initial_world_state

logger = logging.getLogger(__name__)
StreamHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
PARTY_MEMBER_IDS = frozenset({"scout", "analyst", "tactician"})


class ChatTurnResult(TypedDict):
    responses: List[Dict[str, str]]
    journal_events: List[str]
    current_location: str
    environment_objects: Dict[str, Any]
    party_status: Dict[str, Any]
    player_inventory: Dict[str, Any]
    combat_state: Dict[str, Any]


class GraphProtocol(Protocol):
    async def aget_state(self, config: Dict[str, Any]) -> Any:
        ...

    async def aupdate_state(
        self,
        config: Dict[str, Any],
        payload: Dict[str, Any],
        as_node: str,
    ) -> Any:
        ...

    async def ainvoke(self, payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def astream(self, payload: Dict[str, Any], config: Dict[str, Any], stream_mode: str) -> Any:
        ...


class GameServiceError(Exception):
    """Base exception for application-service failures."""


class InvalidChatRequestError(GameServiceError):
    """Raised when API input is invalid for the requested operation."""


class StateAccessError(GameServiceError):
    """Raised when checkpoint state cannot be loaded or updated safely."""


class GraphExecutionError(GameServiceError):
    """Raised when the compiled graph fails to process a turn."""


class GameService:
    """Application service that orchestrates one chat turn end-to-end."""

    def __init__(
        self,
        db_path: str = "memory.db",
        saver_factory: Callable[[str], Any] = AsyncSqliteSaver.from_conn_string,
        graph_builder: Callable[..., GraphProtocol] = build_graph,
        initial_state_factory: Callable[..., Dict[str, Any]] = get_initial_world_state,
        loot_executor: Callable[[Dict[str, Any], Dict[str, Any], str, str], str] = execute_loot,
        memory_service: Optional[MemoryService] = None,
    ) -> None:
        self._db_path = db_path
        self._saver_factory = saver_factory
        self._graph_builder = graph_builder
        self._initial_state_factory = initial_state_factory
        self._loot_executor = loot_executor
        self._memory_service = memory_service or get_default_memory_service()

    async def process_chat_turn(
        self,
        *,
        user_input: str = "",
        intent: Optional[str] = None,
        session_id: str,
        character: Optional[str] = None,
        map_id: Optional[str] = None,
        target: Optional[str] = None,
        source: Optional[str] = None,
        client_player_position: Optional[Dict[str, Any]] = None,
        player_position: Optional[List[Any]] = None,
        stream_handler: Optional[StreamHandler] = None,
    ) -> ChatTurnResult:
        normalized_input = (user_input or "").strip()
        normalized_intent = (intent or "").strip()
        if not normalized_intent and normalized_input:
            normalized_intent = "chat"
        normalized_intent_key = normalized_intent.lower()
        normalized_target = (target or "").strip()
        normalized_source = (source or "").strip().lower()
        if not normalized_input and not normalized_intent:
            raise InvalidChatRequestError(
                "At least one of user_input or intent must be non-empty."
            )
        turn_started_at = time.perf_counter()
        turn_ok = False
        turn_error_type = ""
        emit_telemetry(
            "turn_started",
            session_id=session_id,
            intent=normalized_intent_key or "chat",
            has_user_input=bool(normalized_input),
            user_input_length=len(normalized_input),
        )

        try:
            config = {"configurable": {"thread_id": session_id}}

            async with self._saver_factory(self._db_path) as saver:
                graph = self._graph_builder(checkpointer=saver)
                previous_state = await self._load_checkpoint_state(graph, config)
                previous_journal_len = len(previous_state.get("journal_events") or [])

                if self._requires_state_reinitialization(previous_state, map_id=map_id):
                    previous_state = await self._initialize_world_state(
                        graph,
                        config,
                        map_id=map_id,
                    )
                    previous_journal_len = len(previous_state.get("journal_events") or [])

                previous_state = await self._apply_campaign_intro_if_needed(
                    graph=graph,
                    config=config,
                    state=previous_state,
                    session_id=session_id,
                )
                # Recapture journal length after intro patch so intro entries
                # are excluded from the init_sync response.
                previous_journal_len = len(previous_state.get("journal_events") or [])

                previous_state = await self._apply_client_player_position_to_checkpoint(
                    graph=graph,
                    config=config,
                    state=previous_state,
                    client_player_position=client_player_position,
                    player_position=player_position,
                    session_id=session_id,
                )

                if normalized_intent_key == "init_sync":
                    turn_ok = True
                    chat_result = self._build_chat_result(
                        previous_state,
                        previous_journal_len=previous_journal_len,
                    )
                    # init_sync is a pure state sync contract: no per-turn narrative delta.
                    chat_result["responses"] = []
                    chat_result["journal_events"] = []
                    return chat_result

                if normalized_intent_key in {"background_step", "process_reflections"}:
                    result_state = await self._process_background_step(
                        graph=graph,
                        config=config,
                        previous_state=previous_state,
                        intent_key=normalized_intent_key,
                    )
                    turn_ok = True
                    return self._build_chat_result(
                        result_state,
                        previous_journal_len=previous_journal_len,
                    )

                if normalized_intent_key == "ui_action_loot":
                    result_state = await self._process_loot_action(
                        graph=graph,
                        config=config,
                        previous_state=previous_state,
                        previous_journal_len=previous_journal_len,
                        user_input=normalized_input,
                        character=character,
                        target=normalized_target,
                    )
                    chat_result = self._build_chat_result(
                        result_state,
                        previous_journal_len=previous_journal_len,
                    )
                    await self._ingest_turn_memories(
                        session_id=session_id,
                        state=self._normalize_state(result_state),
                        result=chat_result,
                        user_input=normalized_input,
                    )
                    turn_ok = True
                    return chat_result

                payload: Dict[str, Any] = {"user_input": normalized_input}
                if normalized_intent:
                    payload["intent"] = normalized_intent
                payload["target"] = normalized_target
                payload["source"] = normalized_source
                if normalized_target or normalized_source:
                    payload["intent_context"] = {
                        "action_actor": "player",
                        "action_target": normalized_target.lower(),
                        "source": normalized_source,
                    }

                logger.info(
                    "收到聊天请求: user_input=%r intent=%r session_id=%s",
                    normalized_input,
                    normalized_intent,
                    session_id,
                )
                try:
                    if stream_handler is None:
                        result_state = await graph.ainvoke(payload, config=config)
                    else:
                        last_stream_checkpoint = time.perf_counter()
                        node_timing_totals_ms: Dict[str, int] = {}
                        async for update in graph.astream(
                            payload,
                            config=config,
                            stream_mode="updates",
                        ):
                            if isinstance(update, dict):
                                for node_name, node_state in update.items():
                                    normalized_node_name = str(node_name)
                                    now = time.perf_counter()
                                    timing_ms = max(
                                        0,
                                        int(round((now - last_stream_checkpoint) * 1000)),
                                    )
                                    last_stream_checkpoint = now
                                    node_timing_totals_ms[normalized_node_name] = (
                                        node_timing_totals_ms.get(normalized_node_name, 0)
                                        + timing_ms
                                    )
                                    emit_telemetry(
                                        "node_finished",
                                        session_id=session_id,
                                        node_name=normalized_node_name,
                                        timing_ms=timing_ms,
                                        timing_total_ms=node_timing_totals_ms[normalized_node_name],
                                    )
                                    normalized_state = self._normalize_state(node_state)
                                    stream_payload = {
                                        **normalized_state,
                                        "node_name": normalized_node_name,
                                        "timing_ms": timing_ms,
                                        "timing_total_ms": node_timing_totals_ms[normalized_node_name],
                                        "state": normalized_state,
                                    }
                                    await stream_handler(
                                        normalized_node_name,
                                        stream_payload,
                                    )
                        result_state = await self._load_checkpoint_state(graph, config)
                except Exception as exc:
                    raise GraphExecutionError("Failed to process chat turn.") from exc

                normalized_result_state = self._normalize_state(result_state)
                normalized_result_state = await self._drain_pending_events_if_needed(
                    graph=graph,
                    config=config,
                    state=normalized_result_state,
                )
                chat_result = self._build_chat_result(
                    normalized_result_state,
                    previous_journal_len=previous_journal_len,
                )
                await self._ingest_turn_memories(
                    session_id=session_id,
                    state=normalized_result_state,
                    result=chat_result,
                    user_input=normalized_input,
                )
                turn_ok = True
                return chat_result
        except Exception as exc:
            turn_error_type = exc.__class__.__name__
            raise
        finally:
            emit_telemetry(
                "turn_finished",
                session_id=session_id,
                intent=normalized_intent_key or "chat",
                success=turn_ok,
                error_type=turn_error_type,
                duration_ms=max(0, int(round((time.perf_counter() - turn_started_at) * 1000))),
            )

    async def get_session_state(
        self,
        *,
        session_id: str,
        initialize_if_missing: bool = True,
        map_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load the current session state and optionally run Genesis on empty checkpoints."""
        config = {"configurable": {"thread_id": session_id}}
        async with self._saver_factory(self._db_path) as saver:
            graph = self._graph_builder(checkpointer=saver)
            state = await self._load_checkpoint_state(graph, config)
            if initialize_if_missing and self._requires_state_reinitialization(state, map_id=map_id):
                state = await self._initialize_world_state(graph, config, map_id=map_id)
            return state

    async def reset_session(
        self,
        *,
        session_id: str,
        map_id: Optional[str] = None,
    ) -> ChatTurnResult:
        """Reinitialize one session checkpoint without deleting global DB artifacts."""
        config = {"configurable": {"thread_id": session_id}}
        async with self._saver_factory(self._db_path) as saver:
            graph = self._graph_builder(checkpointer=saver)
            existing_state = await self._load_checkpoint_state(graph, config)
            resolved_map_id = str(map_id or "").strip()
            if not resolved_map_id:
                resolved_map_id = str(
                    (existing_state.get("map_data") or {}).get("id") or ""
                ).strip()
            result_state = await self._initialize_world_state(
                graph,
                config,
                map_id=resolved_map_id or None,
            )
            chat_result = self._build_chat_result(
                result_state,
                previous_journal_len=len(result_state.get("journal_events") or []),
            )
            chat_result["responses"] = []
            chat_result["journal_events"] = []
            return chat_result

    async def get_state_snapshot(
        self,
        *,
        session_id: str,
        initialize_if_missing: bool = True,
        map_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = await self.get_session_state(
            session_id=session_id,
            initialize_if_missing=initialize_if_missing,
            map_id=map_id,
        )
        journal_events = state.get("journal_events") or []
        previous_journal_len = max(0, len(journal_events) - 8)
        result = self._build_chat_result(
            state,
            previous_journal_len=previous_journal_len,
        )
        result["game_state"] = copy.deepcopy(state)
        result["last_node"] = state.get("last_node") or state.get("current_node")
        return result

    async def _load_checkpoint_state(
        self,
        graph: GraphProtocol,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            snapshot = await graph.aget_state(config)  # type: ignore[arg-type]
        except Exception as exc:
            raise StateAccessError("Failed to load session state.") from exc
        return self._normalize_state(getattr(snapshot, "values", {}))

    async def _initialize_world_state(
        self,
        graph: GraphProtocol,
        config: Dict[str, Any],
        *,
        map_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        initial_state = self._build_initial_state(map_id=map_id)
        try:
            await graph.aupdate_state(config, initial_state, as_node=START)  # type: ignore[arg-type]
        except Exception as exc:
            raise StateAccessError("Failed to initialize world state.") from exc
        return await self._load_checkpoint_state(graph, config)

    def _build_initial_state(self, *, map_id: Optional[str]) -> Dict[str, Any]:
        normalized_map_id = str(map_id or "").strip()
        if not normalized_map_id:
            return self._initial_state_factory()

        try:
            return self._initial_state_factory(map_id=normalized_map_id)
        except TypeError:
            signature = inspect.signature(self._initial_state_factory)
            if "map_id" not in signature.parameters:
                logger.warning(
                    "initial_state_factory does not accept map_id; fallback to default map."
                )
                return self._initial_state_factory()
            raise

    def _requires_state_reinitialization(
        self,
        state: Dict[str, Any],
        *,
        map_id: Optional[str],
    ) -> bool:
        entities = state.get("entities")
        if not isinstance(entities, dict) or not entities:
            return True

        requested_map_id = str(map_id or "").strip().lower()
        if not requested_map_id:
            return False

        state_map_id = str((state.get("map_data") or {}).get("id") or "").strip().lower()
        if state_map_id and state_map_id != requested_map_id:
            logger.info(
                "Session map mismatch detected: state_map_id=%s requested_map_id=%s; reinitializing.",
                state_map_id,
                requested_map_id,
            )
            return True

        return False

    async def _drain_pending_events_if_needed(
        self,
        *,
        graph: GraphProtocol,
        config: Dict[str, Any],
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        pending = list(state.get("pending_events") or [])
        if not pending:
            return state

        event_patch = event_drain_node(dict(state))
        if not event_patch:
            return state

        persist_payload: Dict[str, Any] = {}
        for key in (
            "pending_events",
            "entities",
            "environment_objects",
            "player_inventory",
            "flags",
            "journal_events",
            "messages",
            "speaker_responses",
            "reflection_queue",
            "actor_runtime_state",
            "final_response",
            "combat_phase",
            "combat_active",
            "initiative_order",
            "current_turn_index",
            "turn_resources",
        ):
            if key in event_patch:
                persist_payload[key] = event_patch[key]

        if not persist_payload:
            return state

        try:
            await graph.aupdate_state(
                config,
                persist_payload,
                as_node=START,
            )
        except Exception as exc:
            raise StateAccessError("Failed to persist pending event drain patch.") from exc
        return await self._load_checkpoint_state(graph, config)

    async def _apply_campaign_intro_if_needed(
        self,
        *,
        graph: GraphProtocol,
        config: Dict[str, Any],
        state: Dict[str, Any],
        session_id: str,
    ) -> Dict[str, Any]:
        intro_patch = detect_lab_intro_awareness(dict(state))
        if not intro_patch:
            return state
        try:
            await graph.aupdate_state(
                config,
                intro_patch,
                as_node=START,
            )
        except Exception as exc:
            raise StateAccessError("Failed to persist campaign intro patch.") from exc
        _ = session_id
        return await self._load_checkpoint_state(graph, config)

    async def _apply_client_player_position_to_checkpoint(
        self,
        *,
        graph: GraphProtocol,
        config: Dict[str, Any],
        state: Dict[str, Any],
        client_player_position: Optional[Dict[str, Any]],
        player_position: Optional[List[Any]],
        session_id: str,
    ) -> Dict[str, Any]:
        coords = self._extract_client_player_grid_position(
            client_player_position=client_player_position,
            player_position=player_position,
        )
        if coords is None:
            return state

        x, y = coords
        patch = self._build_client_player_position_patch(state=state, x=x, y=y)
        if not patch:
            emit_telemetry(
                "client_player_position_ignored",
                session_id=session_id,
                x=x,
                y=y,
                reason="invalid_or_blocked",
            )
            logger.info(
                "Ignored invalid client player position for session %s: x=%s y=%s",
                session_id,
                x,
                y,
            )
            return state

        try:
            await graph.aupdate_state(
                config,
                patch,
                as_node=START,
            )
        except Exception as exc:
            raise StateAccessError("Failed to persist client player position.") from exc
        return await self._load_checkpoint_state(graph, config)

    @staticmethod
    def _extract_client_player_grid_position(
        *,
        client_player_position: Optional[Dict[str, Any]],
        player_position: Optional[List[Any]],
    ) -> Optional[tuple[int, int]]:
        candidates: List[Any] = []
        if isinstance(client_player_position, dict):
            candidates.append(client_player_position)
        if isinstance(player_position, list):
            candidates.append(player_position)

        for candidate in candidates:
            if isinstance(candidate, dict):
                raw_x = candidate.get("x")
                raw_y = candidate.get("y")
            elif isinstance(candidate, list) and len(candidate) >= 2:
                raw_x = candidate[0]
                raw_y = candidate[1]
            else:
                continue
            if (
                isinstance(raw_x, int)
                and not isinstance(raw_x, bool)
                and isinstance(raw_y, int)
                and not isinstance(raw_y, bool)
            ):
                return raw_x, raw_y
        return None

    @classmethod
    def _build_client_player_position_patch(
        cls,
        *,
        state: Dict[str, Any],
        x: int,
        y: int,
    ) -> Dict[str, Any]:
        if not cls._is_client_player_grid_position_valid(state=state, x=x, y=y):
            return {}

        entities = copy.deepcopy(state.get("entities") or {})
        if not isinstance(entities, dict):
            return {}
        player = entities.get("player")
        if not isinstance(player, dict):
            return {}

        player["x"] = x
        player["y"] = y
        entities["player"] = player
        return {"entities": entities}

    @staticmethod
    def _is_client_player_grid_position_valid(
        *,
        state: Dict[str, Any],
        x: int,
        y: int,
    ) -> bool:
        map_data = state.get("map_data") if isinstance(state, dict) else None
        if not isinstance(map_data, dict):
            return False

        width = map_data.get("width")
        height = map_data.get("height")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or width <= 0
            or height <= 0
        ):
            return False
        if x < 0 or y < 0 or x >= width or y >= height:
            return False

        blocked_tiles = map_data.get("blocked_movement_tiles") or []
        if isinstance(blocked_tiles, list):
            for tile in blocked_tiles:
                if not isinstance(tile, (list, tuple)) or len(tile) < 2:
                    continue
                tile_x, tile_y = tile[0], tile[1]
                if tile_x == x and tile_y == y:
                    return False
        return True

    async def _process_background_step(
        self,
        *,
        graph: GraphProtocol,
        config: Dict[str, Any],
        previous_state: Dict[str, Any],
        intent_key: str,
    ) -> Dict[str, Any]:
        working_state = copy.deepcopy(previous_state)
        max_items = 3 if intent_key == "background_step" else 1
        reflection_patch = await run_reflection_tick(working_state, max_items=max_items)
        if reflection_patch:
            working_state.update(reflection_patch)
        event_patch = event_drain_node(working_state)
        if event_patch:
            working_state.update(event_patch)

        persist_payload: Dict[str, Any] = {}
        for patch in (reflection_patch, event_patch):
            if not isinstance(patch, dict):
                continue
            for key in (
                "reflection_queue",
                "pending_events",
                "journal_events",
                "messages",
                "speaker_responses",
                "flags",
                "actor_runtime_state",
                "final_response",
            ):
                if key in patch:
                    persist_payload[key] = patch[key]

        if not persist_payload:
            return previous_state

        try:
            await graph.aupdate_state(
                config,
                persist_payload,
                as_node=START,
            )
        except Exception as exc:
            raise StateAccessError("Failed to persist background step.") from exc

        return await self._load_checkpoint_state(graph, config)

    async def _process_loot_action(
        self,
        *,
        graph: GraphProtocol,
        config: Dict[str, Any],
        previous_state: Dict[str, Any],
        previous_journal_len: int,
        user_input: str,
        character: Optional[str],
        target: Optional[str],
    ) -> Dict[str, Any]:
        entities = copy.deepcopy(previous_state.get("entities") or {})
        environment_objects = copy.deepcopy(previous_state.get("environment_objects") or {})
        player_inventory = copy.deepcopy(previous_state.get("player_inventory") or {})
        if not entities:
            raise InvalidChatRequestError("No entities in state; cannot loot.")

        character_id = (character or "").strip().lower()
        if not character_id:
            raise InvalidChatRequestError(
                "character is required for ui_action_loot (e.g. analyst)."
            )
        if character_id != "player" and character_id not in entities:
            raise InvalidChatRequestError(f"Unknown character: {character_id}")

        target_id = self._extract_loot_target_id(
            user_input=user_input,
            entities=entities,
            environment_objects=environment_objects,
            explicit_target=target,
        )
        loot_result = mechanics.execute_loot_action(
            {
                "entities": entities,
                "environment_objects": environment_objects,
                "player_inventory": player_inventory,
                "map_data": copy.deepcopy(previous_state.get("map_data") or {}),
                "flags": copy.deepcopy(previous_state.get("flags") or {}),
                "turn_count": int(previous_state.get("turn_count") or 0),
                "intent_context": {
                    "action_actor": "player",
                    "action_target": target_id or "iron_chest",
                },
            }
        )
        if loot_result.get("pending_events"):
            working_state = {
                **previous_state,
                **loot_result,
            }
            event_patch = event_drain_node(working_state)
            if event_patch:
                merged_journal = list(loot_result.get("journal_events") or [])
                merged_journal.extend(list(event_patch.get("journal_events") or []))
                loot_result = {
                    **loot_result,
                    **event_patch,
                    "journal_events": merged_journal,
                }

        persist_payload: Dict[str, Any] = {
            "entities": loot_result.get("entities", entities),
            "environment_objects": loot_result.get("environment_objects", environment_objects),
            "player_inventory": loot_result.get("player_inventory", player_inventory),
            "journal_events": loot_result.get("journal_events", []),
        }
        for key in (
            "pending_events",
            "flags",
            "actor_runtime_state",
            "messages",
            "speaker_responses",
            "reflection_queue",
            "combat_phase",
            "combat_active",
            "initiative_order",
            "current_turn_index",
            "turn_resources",
        ):
            if key in loot_result:
                persist_payload[key] = loot_result[key]
        try:
            await graph.aupdate_state(
                config,
                persist_payload,
                as_node=START,
            )
        except Exception as exc:
            raise StateAccessError("Failed to persist loot action.") from exc

        result_state = await self._load_checkpoint_state(graph, config)
        # 保留原接口结构：loot 分支无对话 responses，仅返回本回合日志与最新状态。
        return result_state

    async def _ingest_turn_memories(
        self,
        *,
        session_id: str,
        state: Dict[str, Any],
        result: ChatTurnResult,
        user_input: str,
    ) -> None:
        if not user_input and not result.get("journal_events"):
            return

        try:
            turn_input = TurnMemoryInput(
                session_id=session_id,
                user_input=str(user_input or ""),
                responses=list(result.get("responses") or []),
                journal_events=[str(item) for item in (result.get("journal_events") or [])],
                current_location=str(result.get("current_location") or ""),
                turn_index=int(state.get("turn_count") or 0),
                party_status=dict(result.get("party_status") or {}),
                flags=dict(state.get("flags") or {}),
            )
            self._memory_service.ingest_turn(turn_input)
        except Exception as exc:  # degrade silently without breaking turn
            logger.warning("memory ingestion failed, degraded silently: %s", exc)

    def _build_chat_result(
        self,
        state: Dict[str, Any],
        *,
        previous_journal_len: int,
    ) -> ChatTurnResult:
        raw_responses = state.get("speaker_responses") or []
        formatted_responses = [
            {"speaker": speaker, "text": text}
            for speaker, text in raw_responses
        ]
        current_journal = state.get("journal_events") or []
        new_journal = (
            current_journal[previous_journal_len:]
            if len(current_journal) > previous_journal_len
            else []
        )
        recent_barks = self._extract_recent_barks_for_turn(
            state=state,
            new_journal=new_journal,
        )
        environment_objects = self._build_environment_objects_payload(state)
        player_inventory = state.get("player_inventory")
        return {
            **(
                {"latest_roll": copy.deepcopy(state.get("latest_roll"))}
                if isinstance(state.get("latest_roll"), dict) and state.get("latest_roll")
                else {}
            ),
            **(
                {"demo_cleared": True}
                if bool(state.get("demo_cleared", False))
                else {}
            ),
            "responses": formatted_responses,
            "journal_events": new_journal,
            "current_location": state.get("current_location", "Unknown"),
            "environment_objects": environment_objects,
            "party_status": self._build_party_status_payload(state),
            "player_inventory": player_inventory if isinstance(player_inventory, dict) else {},
            "combat_state": self._build_combat_state_payload(state, recent_barks=recent_barks),
        }

    @staticmethod
    def _normalize_state(state: Any) -> Dict[str, Any]:
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _extract_recent_barks_for_turn(
        *,
        state: Dict[str, Any],
        new_journal: List[str],
    ) -> List[Dict[str, Any]]:
        if not any("💬 [台词]" in str(line) for line in (new_journal or [])):
            return []
        raw_barks = state.get("recent_barks") or []
        if not isinstance(raw_barks, list):
            return []
        sanitized = [copy.deepcopy(item) for item in raw_barks if isinstance(item, dict)]
        return sanitized

    @staticmethod
    def _build_environment_objects_payload(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        将静态环境物体与可渲染的场景实体合并成前端使用的 environment_objects。
        当前至少保证 hostile 实体（如怪物）不会在 API 层丢失。
        """
        payload = copy.deepcopy(state.get("environment_objects") or {})
        if not isinstance(payload, dict):
            payload = {}
        else:
            sanitized_payload: Dict[str, Any] = {}
            for obj_id, obj in payload.items():
                if not isinstance(obj, dict):
                    continue
                entity_type = str(obj.get("entity_type", obj.get("type", ""))).strip().lower()
                is_hidden = bool(obj.get("is_hidden", False))
                status = str(obj.get("status", "")).strip().lower()
                if entity_type == "trap" and (is_hidden or status == "hidden"):
                    continue
                safe_obj = copy.deepcopy(obj)
                if entity_type == "trap":
                    for internal_key in ("detect_dc", "disarm_dc", "save_dc", "damage", "damage_type", "trigger_radius"):
                        safe_obj.pop(internal_key, None)
                    safe_obj["description"] = "可疑机关 · 具体结构需要靠近处理。"
                sanitized_payload[str(obj_id)] = safe_obj
            payload = sanitized_payload

        entities = state.get("entities") or {}
        if not isinstance(entities, dict):
            return payload

        for entity_id, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            faction = str(entity.get("faction", "")).strip().lower()
            entity_type = str(entity.get("entity_type", "")).strip().lower()
            if faction != "hostile" and entity_type not in {"powder_barrel", "loot_drop", "door", "trap"}:
                continue
            if entity_type == "trap" and bool(entity.get("is_hidden", True)):
                continue
            entity_payload = {
                "id": str(entity_id),
                "type": "entity",
                "name": entity.get("name", str(entity_id)),
                "description": (
                    (
                        f"可破坏地形 · HP {entity.get('hp', '—')}/{entity.get('max_hp', entity.get('hp', '—'))}"
                        if entity_type == "powder_barrel"
                        else (
                            f"战利品堆 · 状态 {entity.get('status', 'open')}"
                            if entity_type == "loot_drop"
                            else (
                                f"门体 · 状态 {'开启' if bool(entity.get('is_open', False)) else '关闭'}"
                                if entity_type == "door"
                                else (
                                    f"陷阱 · 状态 {'隐藏' if bool(entity.get('is_hidden', True)) else '已暴露'}"
                                    if entity_type == "trap"
                                    else f"敌对单位 · HP {entity.get('hp', '—')}/{entity.get('max_hp', entity.get('hp', '—'))}"
                                )
                            )
                        )
                    )
                    + f" · AC {entity.get('ac', '—')} · 位置 {entity.get('position', 'unknown')}"
                ),
                "hp": entity.get("hp"),
                "max_hp": entity.get("max_hp"),
                "ac": entity.get("ac"),
                "status": entity.get("status", "alive"),
                "faction": entity.get("faction", ""),
                "position": entity.get("position", ""),
                "x": entity.get("x"),
                "y": entity.get("y"),
                "inventory": copy.deepcopy(entity.get("inventory") or {}),
            }
            if entity_type in {"powder_barrel", "loot_drop", "door", "trap"}:
                entity_payload["entity_type"] = entity_type
            if entity_type == "door":
                entity_payload["is_open"] = bool(entity.get("is_open", False))
            if entity_type == "trap":
                entity_payload["is_hidden"] = bool(entity.get("is_hidden", True))
            if entity_type == "loot_drop":
                entity_payload["source_name"] = entity.get("source_name")
            payload[str(entity_id)] = entity_payload

        return payload

    @staticmethod
    def _build_combat_state_payload(
        state: Dict[str, Any],
        *,
        recent_barks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            "combat_active": bool(state.get("combat_active", False)),
            "initiative_order": list(state.get("initiative_order") or []),
            "current_turn_index": int(state.get("current_turn_index") or 0),
            "turn_resources": copy.deepcopy(state.get("turn_resources") or {}),
            "recent_barks": copy.deepcopy(recent_barks or []),
        }

    @staticmethod
    def _build_party_status_payload(state: Dict[str, Any]) -> Dict[str, Any]:
        entities = state.get("entities") or {}
        if not isinstance(entities, dict):
            return {}

        party_status: Dict[str, Any] = {}
        for entity_id, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            if not GameService._is_party_member_entity(str(entity_id), entity):
                continue
            party_status[str(entity_id)] = copy.deepcopy(entity)
        return party_status

    @staticmethod
    def _is_party_member_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
        normalized_id = str(entity_id or "").strip().lower()
        if normalized_id == "player":
            return True
        if normalized_id in PARTY_MEMBER_IDS:
            return True

        faction = str(entity.get("faction", "")).strip().lower()
        if faction in {"hostile", "neutral"}:
            return False
        if re.fullmatch(r".+_\d+", normalized_id):
            return False
        return True

    @staticmethod
    def _extract_loot_target_id(
        *,
        user_input: str,
        entities: Dict[str, Any],
        environment_objects: Dict[str, Any],
        explicit_target: Optional[str] = None,
    ) -> str:
        normalized_explicit = str(explicit_target or "").strip().lower()
        if normalized_explicit:
            return normalized_explicit

        normalized_input = str(user_input or "").strip().lower()
        candidates = [
            str(target_id).strip().lower()
            for target_id in list(entities.keys()) + list(environment_objects.keys())
            if str(target_id).strip()
        ]

        # Direct ID match
        for candidate in candidates:
            if candidate and candidate in normalized_input:
                return candidate

        # Alias-based resolution for common targets
        _LOOT_ALIASES: Dict[str, tuple] = {
            "gatekeeper": ("训练无人机", "训练无人机", "gatekeeper"),
            "chest_1": ("study_chest", "书房箱子", "书房的箱子", "书房宝箱", "箱子", "宝箱", "战利品箱", "chest"),
        }
        for target_id, aliases in _LOOT_ALIASES.items():
            if target_id not in candidates:
                continue
            if any(alias in normalized_input for alias in aliases):
                return target_id

        match = re.search(r"(?:loot|搜刮|搜尸|摸尸|拾取)\s+([a-zA-Z0-9_]+)", normalized_input)
        if match:
            return match.group(1).strip().lower()

        return ""


async def process_chat_turn(
    *,
    user_input: str = "",
    intent: Optional[str] = None,
    session_id: str,
    character: Optional[str] = None,
    map_id: Optional[str] = None,
    target: Optional[str] = None,
    source: Optional[str] = None,
    client_player_position: Optional[Dict[str, Any]] = None,
    player_position: Optional[List[Any]] = None,
    stream_handler: Optional[StreamHandler] = None,
    saver_factory: Callable[[str], Any] = AsyncSqliteSaver.from_conn_string,
    graph_builder: Callable[..., GraphProtocol] = build_graph,
    initial_state_factory: Callable[..., Dict[str, Any]] = get_initial_world_state,
    loot_executor: Callable[[Dict[str, Any], Dict[str, Any], str, str], str] = execute_loot,
    memory_service: Optional[MemoryService] = None,
) -> ChatTurnResult:
    """测试友好的函数式入口。"""
    service = GameService(
        saver_factory=saver_factory,
        graph_builder=graph_builder,
        initial_state_factory=initial_state_factory,
        loot_executor=loot_executor,
        memory_service=memory_service,
    )
    return await service.process_chat_turn(
        user_input=user_input,
        intent=intent,
        session_id=session_id,
        character=character,
        map_id=map_id,
        target=target,
        source=source,
        client_player_position=client_player_position,
        player_position=player_position,
        stream_handler=stream_handler,
    )
