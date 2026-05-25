from __future__ import annotations

from typing import Any, Dict, Optional

from core.actors.memory_port import MemorySnippetProvider
from core.actors.views import ActorSelfState, ActorView, DirectorView, PublicEntityView
from core.actors.visibility import (
    build_public_entity_view,
    build_recent_public_events,
    build_visible_history,
    filter_environment_objects_for_actor,
    filter_flags_for_actor,
)
from core.graph.graph_state import GameState


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_inventory(raw_inventory: Any) -> Dict[str, int]:
    inventory = _safe_dict(raw_inventory)
    normalized: Dict[str, int] = {}
    for item_id, count in inventory.items():
        key = str(item_id or "").strip()
        if not key:
            continue
        qty = _coerce_int(count, 0)
        if qty <= 0:
            continue
        normalized[key] = qty
    return normalized


def _normalize_dynamic_states(raw_dynamic_states: Any) -> Dict[str, int]:
    dynamic_states = _safe_dict(raw_dynamic_states)
    normalized: Dict[str, int] = {}
    for state_key, payload in dynamic_states.items():
        key = _normalize_id(state_key)
        if not key:
            continue
        if isinstance(payload, dict):
            value = payload.get("current_value", payload.get("value", 0))
        else:
            value = payload
        normalized[key] = _coerce_int(value, 0)
    return normalized


def build_actor_self_state(
    state: GameState,
    actor_id: str,
) -> ActorSelfState:
    normalized_state = _safe_dict(state)
    normalized_actor_id = _normalize_id(actor_id)
    entities = _safe_dict(normalized_state.get("entities"))
    actor_entity = _safe_dict(entities.get(normalized_actor_id))

    inventory = _normalize_inventory(actor_entity.get("inventory"))
    if not inventory:
        # Compatibility: legacy pipeline may still maintain npc_inventory at top-level.
        inventory = _normalize_inventory(normalized_state.get("npc_inventory"))

    return ActorSelfState(
        actor_id=normalized_actor_id,
        name=str(actor_entity.get("name") or normalized_actor_id),
        hp=_coerce_int(actor_entity.get("hp"), 0),
        max_hp=_coerce_int(actor_entity.get("max_hp", actor_entity.get("hp", 0)), 0),
        inventory=inventory,
        affection=_coerce_int(actor_entity.get("affection"), 0),
        active_buffs=list(actor_entity.get("active_buffs") or []),
        position=str(actor_entity.get("position") or ""),
        dynamic_states=_normalize_dynamic_states(actor_entity.get("dynamic_states")),
    )


def build_other_entities_public_view(
    state: GameState,
    actor_id: str,
) -> Dict[str, PublicEntityView]:
    normalized_state = _safe_dict(state)
    normalized_actor_id = _normalize_id(actor_id)
    entities = _safe_dict(normalized_state.get("entities"))

    other_entities: Dict[str, PublicEntityView] = {}
    for raw_entity_id, raw_entity in entities.items():
        entity_id = _normalize_id(raw_entity_id)
        if not entity_id or entity_id == normalized_actor_id:
            continue
        entity = _safe_dict(raw_entity)
        if not entity:
            continue
        other_entities[entity_id] = build_public_entity_view(entity_id, entity)
    return other_entities


def build_actor_view(
    state: GameState,
    actor_id: str,
    *,
    memory_provider: Optional[MemorySnippetProvider] = None,
) -> ActorView:
    normalized_state = _safe_dict(state)
    normalized_actor_id = _normalize_id(actor_id)

    user_input = str(normalized_state.get("user_input", "") or "")
    memory_snippets = []
    if memory_provider is not None and user_input.strip():
        try:
            memory_snippets = memory_provider.retrieve_for_actor(
                actor_id=normalized_actor_id,
                query=user_input,
                top_k=2,
                current_location=str(normalized_state.get("current_location", "") or ""),
                turn_index=_coerce_int(normalized_state.get("turn_count"), 0),
            )
        except TypeError:
            # Backward compatibility for providers that only implement actor_id/query/top_k.
            memory_snippets = memory_provider.retrieve_for_actor(
                actor_id=normalized_actor_id,
                query=user_input,
                top_k=2,
            )

    return ActorView(
        actor_id=normalized_actor_id,
        user_input=user_input,
        intent=str(normalized_state.get("intent", "chat") or "chat"),
        intent_context=dict(normalized_state.get("intent_context") or {}),
        is_probing_secret=bool(normalized_state.get("is_probing_secret", False)),
        self_state=build_actor_self_state(normalized_state, normalized_actor_id),
        other_entities=build_other_entities_public_view(normalized_state, normalized_actor_id),
        current_location=str(normalized_state.get("current_location", "Unknown Location")),
        time_of_day=str(normalized_state.get("time_of_day", "未知时段")),
        turn_count=_coerce_int(normalized_state.get("turn_count"), 0),
        visible_environment_objects=filter_environment_objects_for_actor(
            normalized_state,
            normalized_actor_id,
        ),
        visible_flags=filter_flags_for_actor(
            normalized_state.get("flags") or {},
            normalized_actor_id,
            state=normalized_state,
        ),
        visible_history=build_visible_history(
            normalized_state.get("messages") or [],
            actor_id=normalized_actor_id,
            limit=12,
        ),
        recent_public_events=build_recent_public_events(
            normalized_state.get("journal_events") or [],
            limit=8,
        ),
        latest_roll=dict(normalized_state.get("latest_roll") or {}),
        memory_snippets=list(memory_snippets),
    )


def build_director_view(state: GameState) -> DirectorView:
    return DirectorView(state=dict(state or {}))
