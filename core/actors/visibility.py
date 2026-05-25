from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from core.actors.views import PublicEntityView, VisibleMessage

PUBLIC_FLAG_PREFIXES = (
    "world_",
    "quest_",
    "combat_",
    "public_",
)
PARTY_DEFAULT_ACTOR_IDS = frozenset({"player", "analyst", "scout", "tactician"})
VISIBILITY_SCOPE_PUBLIC = "public"
VISIBILITY_SCOPE_PARTY = "party"
VISIBILITY_SCOPE_ACTOR = "actor"
VISIBILITY_SCOPE_HIDDEN = "hidden"

PUBLIC_ENTITY_FIELDS = {
    "name",
    "position",
    "status",
    "faction",
    "entity_type",
    "hp",
    "max_hp",
}

SELF_ONLY_ENTITY_FIELDS = {
    "inventory",
    "affection",
    "active_buffs",
    "dynamic_states",
    "secret_objective",
}

_SPEAKER_TAG_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*[:：]\s*(.*)$", flags=re.DOTALL)


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_visibility_scope(value: Any, *, default_scope: str) -> str:
    scope = str(value or "").strip().lower()
    if not scope:
        return default_scope
    if scope == "private":
        return VISIBILITY_SCOPE_HIDDEN
    if scope in {
        VISIBILITY_SCOPE_PUBLIC,
        VISIBILITY_SCOPE_PARTY,
        VISIBILITY_SCOPE_ACTOR,
        VISIBILITY_SCOPE_HIDDEN,
    }:
        return scope
    return default_scope


def _legacy_flag_scope(key: str) -> str:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return VISIBILITY_SCOPE_HIDDEN
    if normalized_key.startswith(PUBLIC_FLAG_PREFIXES):
        return VISIBILITY_SCOPE_PUBLIC
    return VISIBILITY_SCOPE_HIDDEN


def _extract_policy_payload(raw_payload: Any) -> tuple[Any, Dict[str, Any]]:
    if not isinstance(raw_payload, Mapping):
        return raw_payload, {}
    has_policy_shape = "visibility" in raw_payload or "value" in raw_payload
    if not has_policy_shape:
        return raw_payload, {}
    visibility = _safe_dict(raw_payload.get("visibility"))
    return raw_payload.get("value", False), visibility


def _normalize_actor_list(raw_visibility: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    raw_actors: Sequence[Any] = []
    if isinstance(raw_visibility.get("actors"), list):
        raw_actors = list(raw_visibility.get("actors") or [])
    elif raw_visibility.get("actor_id") is not None:
        raw_actors = [raw_visibility.get("actor_id")]
    elif raw_visibility.get("actor") is not None:
        raw_actors = [raw_visibility.get("actor")]

    for raw_actor in raw_actors:
        actor_id = _normalize_id(raw_actor)
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        out.append(actor_id)
    return out


def _extract_flag_scalar_value(raw_value: Any) -> Any:
    value, _ = _extract_policy_payload(raw_value)
    return value


def _resolve_flag_value_from_state(state: Mapping[str, Any], flag_key: str) -> Any:
    flags = _safe_dict(state.get("flags"))
    normalized_flag_key = str(flag_key or "").strip()
    if not normalized_flag_key:
        return None

    if "." not in normalized_flag_key:
        return _extract_flag_scalar_value(flags.get(normalized_flag_key))

    current: Any = flags
    for part in [p for p in normalized_flag_key.split(".") if p]:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return _extract_flag_scalar_value(current)


def _evaluate_reveal_condition(
    *,
    state: Mapping[str, Any],
    actor_id: str,
    rule: Any,
) -> bool:
    if rule is None:
        return True
    if isinstance(rule, bool):
        return rule
    if not isinstance(rule, Mapping):
        return False

    all_rules = rule.get("all")
    if isinstance(all_rules, list):
        return all(
            _evaluate_reveal_condition(state=state, actor_id=actor_id, rule=sub_rule)
            for sub_rule in all_rules
        )
    any_rules = rule.get("any")
    if isinstance(any_rules, list):
        return any(
            _evaluate_reveal_condition(state=state, actor_id=actor_id, rule=sub_rule)
            for sub_rule in any_rules
        )
    if "not" in rule:
        return not _evaluate_reveal_condition(
            state=state,
            actor_id=actor_id,
            rule=rule.get("not"),
        )

    actor_in = rule.get("actor_in")
    if isinstance(actor_in, list):
        return _normalize_id(actor_id) in {
            _normalize_id(item) for item in actor_in if _normalize_id(item)
        }

    turn_at_least = rule.get("turn_at_least")
    if turn_at_least is not None:
        try:
            threshold = int(turn_at_least)
        except (TypeError, ValueError):
            threshold = 0
        try:
            current_turn = int(state.get("turn_count") or 0)
        except (TypeError, ValueError):
            current_turn = 0
        return current_turn >= threshold

    if "flag" in rule:
        flag_key = str(rule.get("flag") or "").strip()
        if not flag_key:
            return False
        expected = rule.get("equals", True)
        actual = _resolve_flag_value_from_state(state, flag_key)
        return actual == expected

    return False


def _is_actor_party_member(state: Mapping[str, Any], actor_id: str) -> bool:
    normalized_actor_id = _normalize_id(actor_id)
    if not normalized_actor_id:
        return False
    if normalized_actor_id in PARTY_DEFAULT_ACTOR_IDS:
        return True
    entities = _safe_dict(state.get("entities"))
    entity = _safe_dict(entities.get(normalized_actor_id))
    if not entity:
        return False
    faction = _normalize_id(entity.get("faction"))
    return faction in {"party", "player"}


def _is_visibility_allowed(
    *,
    scope: str,
    actor_id: str,
    state: Mapping[str, Any],
    visibility: Mapping[str, Any],
) -> bool:
    reveal_rule = visibility.get("reveal_when", visibility.get("reveal_condition"))
    reveal_passed = _evaluate_reveal_condition(
        state=state,
        actor_id=actor_id,
        rule=reveal_rule,
    )
    if not reveal_passed:
        return False

    if scope == VISIBILITY_SCOPE_PUBLIC:
        return True
    if scope == VISIBILITY_SCOPE_PARTY:
        return _is_actor_party_member(state, actor_id)

    allowed_actor_ids = _normalize_actor_list(visibility)
    if scope == VISIBILITY_SCOPE_ACTOR:
        if not allowed_actor_ids:
            return False
        return _normalize_id(actor_id) in set(allowed_actor_ids)
    if scope == VISIBILITY_SCOPE_HIDDEN:
        if not visibility:
            return False
        if not allowed_actor_ids:
            return True
        return _normalize_id(actor_id) in set(allowed_actor_ids)
    return False


def _normalize_role(value: Any) -> str:
    role = _normalize_id(value)
    if role in {"assistant", "ai"}:
        return "assistant"
    if role in {"user", "human"}:
        return "user"
    return "user"


def _extract_message_parts(raw_message: Any) -> tuple[str, str, str]:
    if isinstance(raw_message, Mapping):
        role = _normalize_role(raw_message.get("role"))
        content = str(raw_message.get("content") or "").strip()
        speaker_id = _normalize_id(raw_message.get("name"))
        return role, speaker_id, content

    role = _normalize_role(getattr(raw_message, "type", "user"))
    content = str(getattr(raw_message, "content", "") or "").strip()
    speaker_id = _normalize_id(getattr(raw_message, "name", ""))
    return role, speaker_id, content


def _extract_assistant_speaker_and_content(
    *,
    speaker_id: str,
    content: str,
    actor_id: str,
) -> tuple[str, str]:
    normalized_speaker = _normalize_id(speaker_id)
    normalized_content = str(content or "").strip()

    tagged = _SPEAKER_TAG_PATTERN.match(normalized_content)
    if tagged:
        tagged_speaker = _normalize_id(tagged.group(1))
        tagged_content = str(tagged.group(2) or "").strip()
        if tagged_speaker:
            normalized_speaker = tagged_speaker
        if tagged_content:
            normalized_content = tagged_content

    if not normalized_speaker:
        normalized_speaker = _normalize_id(actor_id) or "assistant"
    return normalized_speaker, normalized_content


def filter_flags_for_actor(
    flags: Dict[str, Any],
    actor_id: str,
    *,
    state: Any = None,
) -> Dict[str, bool]:
    source = _safe_dict(flags)
    state_map = _safe_dict(state)
    if "flags" not in state_map:
        state_map = {**state_map, "flags": source}
    visible: Dict[str, bool] = {}
    for key, raw_value in source.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        flag_value, visibility = _extract_policy_payload(raw_value)
        scope = _normalize_visibility_scope(
            visibility.get("scope"),
            default_scope=_legacy_flag_scope(normalized_key),
        )
        if not _is_visibility_allowed(
            scope=scope,
            actor_id=actor_id,
            state=state_map,
            visibility=visibility,
        ):
            continue
        visible[normalized_key] = bool(flag_value)
    return visible


def filter_environment_objects_for_actor(
    state: Any,
    actor_id: str,
) -> Dict[str, Dict[str, Any]]:
    state_map = _safe_dict(state)
    environment = _safe_dict(state_map.get("environment_objects"))
    visible: Dict[str, Dict[str, Any]] = {}

    for object_id, raw_object in environment.items():
        object_data = _safe_dict(raw_object)
        if not object_data:
            continue
        visibility = _safe_dict(object_data.get("visibility"))
        if visibility:
            scope = _normalize_visibility_scope(
                visibility.get("scope"),
                default_scope=VISIBILITY_SCOPE_PUBLIC,
            )
            if not _is_visibility_allowed(
                scope=scope,
                actor_id=actor_id,
                state=state_map,
                visibility=visibility,
            ):
                continue

        entity_type = _normalize_id(object_data.get("entity_type", object_data.get("type")))
        status = _normalize_id(object_data.get("status"))
        is_hidden = bool(object_data.get("is_hidden", False))
        if not visibility and entity_type == "trap" and (is_hidden or status == "hidden"):
            continue

        sanitized = dict(object_data)
        sanitized.pop("visibility", None)
        sanitized.pop("_visibility", None)
        visible[str(object_id)] = sanitized
    return visible


def build_visible_history(messages: List[Any], actor_id: str, limit: int = 12) -> List[VisibleMessage]:
    bounded = list(messages or [])
    if limit > 0:
        bounded = bounded[-limit:]

    normalized: List[VisibleMessage] = []
    for raw_message in bounded:
        role, speaker_id, content = _extract_message_parts(raw_message)
        if not content:
            continue

        if role == "assistant":
            speaker_id, content = _extract_assistant_speaker_and_content(
                speaker_id=speaker_id,
                content=content,
                actor_id=actor_id,
            )
        elif not speaker_id:
            speaker_id = "user"

        normalized.append(
            VisibleMessage(
                role=role,
                speaker_id=speaker_id,
                content=content,
            )
        )
    return normalized


def build_recent_public_events(journal_events: List[str], limit: int = 8) -> List[str]:
    items = [str(item) for item in (journal_events or [])]
    if limit <= 0:
        return []
    return items[-limit:]


def is_party_member_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_id(entity_id)
    if normalized_id in {"player", "analyst", "scout", "tactician"}:
        return True
    faction = _normalize_id(entity.get("faction"))
    return faction not in {"hostile"}


def build_public_entity_view(entity_id: str, entity: Dict[str, Any]) -> PublicEntityView:
    return PublicEntityView(
        entity_id=entity_id,
        name=str(entity.get("name") or entity_id),
        position=str(entity.get("position") or ""),
        status=str(entity.get("status") or ""),
        faction=str(entity.get("faction") or ""),
        entity_type=str(entity.get("entity_type") or entity.get("type") or ""),
        is_party_member=is_party_member_entity(entity_id, entity),
    )
