"""
Game Mechanics Module (Model Layer)
Pure logic and calculation functions - no UI dependencies
"""

import ast
import copy
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from core.campaigns.hazard_lab import (
    detect_gatekeeper_boss_resolution_context,
    detect_poison_trap_trigger_context,
    detect_secret_study_entry_context,
)
from core.engine.physics import DEBUG_ALWAYS_PASS_CHECKS
from core.events.models import DomainEvent, event_to_dict
from core.systems.dice import roll_d20
from core.systems.inventory import get_registry
from core.systems.maps import get_map_data
from core.systems.pathfinding import a_star_path, check_line_of_sight
from core.systems.spells import get_spell_data, resolve_spell_id

logger = logging.getLogger(__name__)

DEFAULT_ATTACK_BONUS = 4
DEFAULT_DAMAGE_BONUS = 2
DEFAULT_DAMAGE_DIE_SIDES = 8
DEFAULT_EQUIPMENT = {"main_hand": None, "ranged": None, "armor": None}
EMPTY_EQUIPMENT = {"main_hand": None, "ranged": None, "armor": None}
LOOTABLE_STATUSES = frozenset({"dead", "open", "opened"})
USE_ITEM_INTENTS = frozenset({"USE_ITEM", "CONSUME"})
MOVE_INTENTS = frozenset({"MOVE", "APPROACH"})
EQUIP_INTENTS = frozenset({"EQUIP", "UNEQUIP"})
PLAYER_TARGET_ALIASES = frozenset({"我", "自己", "玩家", "me", "player"})
PLAYER_SIDE_ENTITY_IDS = frozenset({"player", "scout", "analyst", "tactician"})
RANGED_ATTACK_HINTS = (
    "射击",
    "射箭",
    "开弓",
    "拉弓",
    "远程",
    "短弓",
    "弩",
    "bow",
    "crossbow",
    "shoot",
    "shot",
)
SPELLCASTER_DEFAULT_SLOTS = {
    "analyst": {"level_1": 2},
    "drone_support": {"level_1": 1},
}
DEFAULT_SPELL_SAVE_DC = 13
COORDINATE_TARGET_PATTERN = re.compile(r"^\s*(-?\d+)\s*[,，]\s*(-?\d+)\s*$")
OBSTACLE_TYPE_DISPLAY = {
    "rock": "岩石",
    "campfire": "篝火",
    "door": "门",
    "trap": "陷阱",
    "transition_zone": "传送区域",
    "wall": "墙体",
    "tree": "树木",
}
STATUS_EFFECT_LIBRARY: Dict[str, Dict[str, Any]] = {
    "hidden": {
        "name": "潜行",
        "description": "脱战状态下隐藏自身，进入敌方视野时触发潜行对抗检定。",
    },
    "surprised": {
        "name": "受惊",
        "description": "回合开始时跳过本回合全部行动资源。",
    },
    "prone": {
        "name": "倒地",
        "description": "近战攻击该目标获得优势；目标回合开始时需消耗一半移动力起身。",
    },
    "poisoned": {
        "name": "中毒",
        "description": "回合开始时受到 1d4 毒素伤害。",
    },
}
REST_CLEARABLE_NEGATIVE_EFFECTS = frozenset({"poisoned", "prone", "surprised"})
BARK_TRIGGER_TYPES = frozenset(
    {
        "CRITICAL_HIT",
        "CRITICAL_MISS",
        "KILL",
        "ENVIRONMENTAL_SHOVE",
    }
)


def _scout_memory_echo_type_from_state(state: Any) -> str:
    flags = state.get("flags") if isinstance(state, dict) and isinstance(state.get("flags"), dict) else {}
    sided_flag = flags.get("hazard_lab_player_sided_with_scout")
    if sided_flag is True:
        return "sided_with_player"
    if (
        sided_flag is False
        and "hazard_lab_player_sided_with_scout" in flags
        and bool(flags.get("hazard_lab_scout_mocked_gatekeeper"))
    ):
        return "rebuked_by_player"

    runtime_state = (
        state.get("actor_runtime_state")
        if isinstance(state, dict) and isinstance(state.get("actor_runtime_state"), dict)
        else {}
    )
    scout_state = runtime_state.get("scout") if isinstance(runtime_state.get("scout"), dict) else {}
    notes = "\n".join(str(item) for item in scout_state.get("memory_notes", []) if item is not None)
    notes_lower = notes.lower()
    if any(token in notes for token in ("训斥", "闭嘴", "记住这笔账", "羞辱")) or any(
        token in notes_lower for token in ("rebuke", "humiliat", "insult")
    ):
        return "rebuked_by_player"
    if any(token in notes for token in ("一起嘲笑", "默契", "同调", "满意")) or any(
        token in notes_lower for token in ("side_with", "sided", "complicit")
    ):
        return "sided_with_player"
    return ""


def _apply_scout_memory_echo_journal(
    state: Any,
    flags: Dict[str, Any],
    journal_events: List[str],
) -> None:
    memory_state = dict(state or {}) if isinstance(state, dict) else {}
    memory_state["flags"] = flags
    memory_type = _scout_memory_echo_type_from_state(memory_state)
    if memory_type not in {"rebuked_by_player", "sided_with_player"}:
        return
    journal_events.append(f"[记忆回响] scout -> {memory_type}")
    flags["hazard_lab_scout_memory_echo_seen"] = True
    if memory_type == "rebuked_by_player":
        flags["hazard_lab_scout_rebuke_echo_seen"] = True
        journal_events.append("💬 [台词] scout: \"现在又需要我了？上次你让我闭嘴时可没这么客气。\"")
    else:
        flags["hazard_lab_scout_complicity_echo_seen"] = True
        journal_events.append("💬 [台词] scout: \"又要一起做漂亮坏事？我喜欢这种默契。\"")


def calculate_ability_modifier(ability_score: int) -> int:
    """
    Calculate d20 5e ability modifier from ability score.
    
    Formula: (ability_score - 10) // 2
    
    Args:
        ability_score: The ability score (typically 1-20)
    
    Returns:
        int: The ability modifier
    """
    return (ability_score - 10) // 2


def get_ability_modifiers(ability_scores: dict) -> dict:
    """
    Calculate all ability modifiers from ability scores.
    
    Args:
        ability_scores: Dictionary of ability scores (e.g., {"STR": 13, "DEX": 14, ...})
    
    Returns:
        dict: Dictionary of ability modifiers with same keys
    """
    return {ability: calculate_ability_modifier(score) for ability, score in ability_scores.items()}


def normalize_ability_name(ability_name: str) -> Optional[str]:
    """
    Normalize ability name to standard format (STR, DEX, CON, INT, WIS, CHA).
    Handles common abbreviations and case variations.
    
    Args:
        ability_name: User input ability name (e.g., "wis", "CHA", "charisma")
    
    Returns:
        Optional[str]: Standardized ability name (STR, DEX, CON, INT, WIS, CHA) or None if not found
    """
    ability_name = ability_name.upper().strip()
    
    # Mapping of common abbreviations to standard names
    ability_map = {
        "STR": "STR", "STRENGTH": "STR",
        "DEX": "DEX", "DEXTERITY": "DEX",
        "CON": "CON", "CONSTITUTION": "CON",
        "INT": "INT", "INTELLIGENCE": "INT",
        "WIS": "WIS", "WISDOM": "WIS",
        "CHA": "CHA", "CHARISMA": "CHA"
    }
    
    return ability_map.get(ability_name)


def _normalize_entity_id(entity_id: Any) -> str:
    return str(entity_id or "").strip().lower()


def _display_entity_name(entity: Dict[str, Any], fallback_id: str) -> str:
    name = str(entity.get("name") or "").strip()
    if name:
        return name
    if fallback_id == "player":
        return "玩家"
    return fallback_id.replace("_", " ").strip().title() or "未知目标"


def _normalize_status_effects(value: Any) -> List[Dict[str, Any]]:
    effects: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return effects
    for raw_effect in value:
        if not isinstance(raw_effect, dict):
            continue
        effect_type = str(raw_effect.get("type") or "").strip().lower()
        if not effect_type:
            continue
        duration = max(0, _coerce_int(raw_effect.get("duration"), 0))
        effects.append({"type": effect_type, "duration": duration})
    return effects


def _normalize_dynamic_states(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for state_key, payload in value.items():
        sid = str(state_key or "").strip().lower()
        if not sid:
            continue
        if isinstance(payload, dict):
            current_value = payload.get("current_value", payload.get("value", 0))
            normalized[sid] = {
                **payload,
                "current_value": _coerce_int(current_value, 0),
            }
            continue
        normalized[sid] = {"current_value": _coerce_int(payload, 0)}
    return normalized


def _ensure_dynamic_states(entity: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    dynamic_states = _normalize_dynamic_states(entity.get("dynamic_states"))
    entity["dynamic_states"] = dynamic_states
    return dynamic_states


def _ensure_status_effects(entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    effects = _normalize_status_effects(entity.get("status_effects"))
    entity["status_effects"] = effects
    return effects


def _has_status_effect(entity: Dict[str, Any], effect_type: str) -> bool:
    normalized_type = str(effect_type or "").strip().lower()
    if not normalized_type:
        return False
    for effect in _ensure_status_effects(entity):
        if str(effect.get("type") or "").strip().lower() == normalized_type:
            return True
    return False


def _remove_status_effect(entity: Dict[str, Any], effect_type: str) -> bool:
    normalized_type = str(effect_type or "").strip().lower()
    if not normalized_type:
        return False
    effects = _ensure_status_effects(entity)
    kept = [effect for effect in effects if str(effect.get("type") or "").strip().lower() != normalized_type]
    removed = len(kept) != len(effects)
    entity["status_effects"] = kept
    return removed


def _generate_bark_for_event(
    *,
    entity_id: str,
    entity_name: str,
    event_type: str,
    target_name: str,
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    normalized_event = str(event_type or "").strip().upper()
    if normalized_event not in BARK_TRIGGER_TYPES:
        return None
    try:
        from core.llm.narrative import generate_combat_bark

        text = str(
            generate_combat_bark(
                character_name=entity_name,
                event_type=normalized_event,
                target_name=target_name,
                context=context,
            )
            or ""
        ).strip()
    except Exception as exc:
        logger.warning("Combat bark generation failed: %s", exc)
        return None
    if not text:
        return None
    return {
        "entity": entity_id,
        "entity_name": entity_name,
        "event_type": normalized_event,
        "target": target_name,
        "text": text,
    }


def _add_or_refresh_status_effect(entity: Dict[str, Any], effect_type: str, duration: int) -> None:
    normalized_type = str(effect_type or "").strip().lower()
    if not normalized_type:
        return
    duration = max(0, int(duration or 0))
    effects = _ensure_status_effects(entity)
    for effect in effects:
        if str(effect.get("type") or "").strip().lower() == normalized_type:
            effect["duration"] = max(_coerce_int(effect.get("duration"), 0), duration)
            return
    effects.append({"type": normalized_type, "duration": duration})


def _apply_start_of_turn_status_effects(
    *,
    entity_id: str,
    entity: Dict[str, Any],
    resources: Dict[str, Any],
) -> List[str]:
    journal_events: List[str] = []
    effects = _ensure_status_effects(entity)
    if not effects:
        return journal_events

    actor_name = _display_entity_name(entity, entity_id)
    for effect in effects:
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect_type == "surprised":
            resources["action"] = 0
            resources["bonus_action"] = 0
            resources["movement"] = 0
            journal_events.append(f"😵 [状态结算] {actor_name} 处于受惊状态，跳过本回合。")
        elif effect_type == "poisoned":
            poison_roll = parse_dice_string("1d4")
            poison_damage = max(1, poison_roll)
            current_hp = _coerce_int(entity.get("hp"), 0)
            max_hp = _coerce_int(entity.get("max_hp"), current_hp)
            new_hp = max(0, current_hp - poison_damage)
            entity["hp"] = new_hp
            entity["max_hp"] = max_hp
            entity["status"] = "dead" if new_hp <= 0 else "alive"
            journal_events.append(
                f"🤢 [状态结算] {actor_name} 受到 中毒 影响，受到 {poison_damage} 点毒素伤害。"
            )
            if new_hp <= 0:
                journal_events.append(f"☠️ [战斗结果] {actor_name} 倒下了。")
        elif effect_type == "prone":
            current_movement = max(0, _coerce_int(resources.get("movement"), 0))
            stand_cost = current_movement // 2
            if stand_cost > 0:
                resources["movement"] = max(0, current_movement - stand_cost)
                journal_events.append(
                    f"🧍 [状态结算] {actor_name} 从倒地中起身，消耗了 {stand_cost} 点移动力。"
                )
    return journal_events


def _apply_end_of_turn_status_effects(
    *,
    entity_id: str,
    entity: Dict[str, Any],
) -> List[str]:
    journal_events: List[str] = []
    effects = _ensure_status_effects(entity)
    if not effects:
        return journal_events

    actor_name = _display_entity_name(entity, entity_id)
    remaining_effects: List[Dict[str, Any]] = []
    for effect in effects:
        effect_type = str(effect.get("type") or "").strip().lower()
        next_duration = _coerce_int(effect.get("duration"), 0) - 1
        if next_duration <= 0:
            effect_name = STATUS_EFFECT_LIBRARY.get(effect_type, {}).get("name", effect_type)
            journal_events.append(f"🧹 [状态结算] {actor_name} 的{effect_name}状态已解除。")
            continue
        remaining_effects.append({"type": effect_type, "duration": next_duration})
    entity["status_effects"] = remaining_effects
    return journal_events


def _build_player_combatant() -> Dict[str, Any]:
    return {
        "id": "player",
        "name": "玩家",
        "faction": "player",
        "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "speed": 30,
        "hp": 20,
        "max_hp": 20,
        "ac": 10,
        "status": "alive",
        "inventory": {},
        "equipment": dict(DEFAULT_EQUIPMENT),
        "position": "camp_center",
        "x": 4,
        "y": 9,
        "active_buffs": [],
        "status_effects": [],
        "dynamic_states": {},
        "affection": 0,
    }


def _ensure_actor_entity(
    *,
    actor_id: str,
    entities: Dict[str, Any],
    state: Any,
) -> Dict[str, Any]:
    if actor_id == "player":
        existing = entities.get("player")
        if isinstance(existing, dict):
            existing.setdefault("name", "玩家")
            existing.setdefault("faction", "player")
            existing.setdefault(
                "ability_scores",
                {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            )
            existing.setdefault("speed", 30)
            existing.setdefault("max_hp", existing.get("hp", 20))
            existing.setdefault("status", "alive")
            existing.setdefault("inventory", {})
            _ensure_status_effects(existing)
            _ensure_dynamic_states(existing)
            _get_equipment(existing)
            return existing

        fallback = state.get("party_status", {}).get("player") if isinstance(state, dict) else None
        player_data = _build_player_combatant()
        if isinstance(fallback, dict):
            player_data["hp"] = int(fallback.get("hp", player_data["hp"]))
            player_data["max_hp"] = int(fallback.get("max_hp", player_data["max_hp"]))
            player_data["status"] = str(fallback.get("status", player_data["status"]))
        entities["player"] = player_data
        return entities["player"]

    actor = entities.get(actor_id)
    if not isinstance(actor, dict):
        actor = {
            "name": actor_id.replace("_", " ").title(),
            "faction": "party" if actor_id in PLAYER_SIDE_ENTITY_IDS else "neutral",
            "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "speed": 30,
            "hp": 20,
            "max_hp": 20,
            "status": "alive",
            "inventory": {},
            "equipment": dict(EMPTY_EQUIPMENT),
        }
        entities[actor_id] = actor
    actor.setdefault("max_hp", actor.get("hp", 20))
    actor.setdefault("speed", 30)
    actor.setdefault("status", "alive")
    actor.setdefault("inventory", {})
    _ensure_status_effects(actor)
    _ensure_dynamic_states(actor)
    _get_equipment(actor)
    return actor


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _target_text_matches(normalized_query: str, candidate_id: str, candidate_name: str) -> bool:
    if (
        normalized_query in candidate_id
        or normalized_query in candidate_name
        or candidate_id in normalized_query
        or candidate_name in normalized_query
    ):
        return True

    chest_aliases = ("宝箱", "箱子", "铁箱子", "chest")
    if any(alias in normalized_query for alias in chest_aliases):
        return "chest" in candidate_id or "箱" in candidate_name

    drone_aliases = ("训练无人机", "训练无人机", "drone")
    if any(alias in normalized_query for alias in drone_aliases):
        return "drone" in candidate_id or "训练无人机" in candidate_name or "训练无人机" in candidate_name

    fire_aliases = ("篝火", "营火", "火堆", "campfire", "fire")
    if any(alias in normalized_query for alias in fire_aliases):
        return (
            "campfire" in candidate_id
            or "篝火" in candidate_name
            or "营火" in candidate_name
            or "火堆" in candidate_name
        )

    return False


def _target_aliases(target: Dict[str, Any]) -> List[str]:
    aliases: List[str] = []
    raw_aliases = target.get("alias_ids") or target.get("aliases") or []
    if isinstance(raw_aliases, str):
        aliases.append(raw_aliases)
    elif isinstance(raw_aliases, (list, tuple, set)):
        aliases.extend(str(alias) for alias in raw_aliases if str(alias or "").strip())
    raw_alias = target.get("alias_id")
    if raw_alias:
        aliases.append(str(raw_alias))
    return aliases


def _resolve_target_reference(
    *,
    target_id: str,
    entities: Dict[str, Any],
    environment_objects: Dict[str, Any],
) -> tuple[str, Optional[Dict[str, Any]], str]:
    normalized_target = _normalize_entity_id(target_id)
    if not normalized_target:
        return "", None, ""

    if normalized_target in PLAYER_TARGET_ALIASES:
        normalized_target = "player"

    if normalized_target in entities and isinstance(entities[normalized_target], dict):
        target = entities[normalized_target]
        return normalized_target, target, _display_entity_name(target, normalized_target)

    if normalized_target in environment_objects and isinstance(environment_objects[normalized_target], dict):
        target = environment_objects[normalized_target]
        return normalized_target, target, str(target.get("name") or normalized_target.replace("_", " ").title())

    normalized_query = _normalize_match_text(target_id)
    if not normalized_query:
        return "", None, ""

    search_spaces = (
        ("environment", environment_objects),
        ("entity", entities),
    )
    for _, collection in search_spaces:
        for actual_id, target in collection.items():
            if not isinstance(target, dict):
                continue
            candidate_id = _normalize_match_text(actual_id)
            candidate_name = _normalize_match_text(target.get("name", ""))
            candidate_aliases = [_normalize_match_text(alias) for alias in _target_aliases(target)]
            if not any((candidate_id, candidate_name, *candidate_aliases)):
                continue
            alias_matches = any(
                alias and (normalized_query in alias or alias in normalized_query)
                for alias in candidate_aliases
            )
            if alias_matches or _target_text_matches(normalized_query, candidate_id, candidate_name):
                display_name = (
                    _display_entity_name(target, str(actual_id))
                    if collection is entities
                    else str(target.get("name") or str(actual_id).replace("_", " ").title())
                )
                return str(actual_id).strip().lower(), target, display_name

    return normalized_target, None, normalized_target.replace("_", " ").title()


def _resolve_move_target(
    *,
    target_id: str,
    entities: Dict[str, Any],
    environment_objects: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], str]:
    _, target, target_name = _resolve_target_reference(
        target_id=target_id,
        entities=entities,
        environment_objects=environment_objects,
    )
    return target, target_name


def _parse_coordinate_target(target_query: str) -> Optional[Tuple[int, int]]:
    matched = COORDINATE_TARGET_PATTERN.match(str(target_query or "").strip())
    if not matched:
        return None
    return int(matched.group(1)), int(matched.group(2))


def _move_toward_target(
    *,
    actor_x: int,
    actor_y: int,
    target_x: int,
    target_y: int,
) -> tuple[int, int]:
    dx = target_x - actor_x
    dy = target_y - actor_y

    # 已经与目标相邻（含对角相邻）时，不再移动，避免棋子重叠。
    if abs(dx) <= 1 and abs(dy) <= 1:
        return actor_x, actor_y

    if abs(dx) > abs(dy):
        return target_x - (1 if dx > 0 else -1), target_y

    return target_x, target_y - (1 if dy > 0 else -1)


def _chebyshev_distance(
    *,
    actor_x: int,
    actor_y: int,
    target_x: int,
    target_y: int,
) -> int:
    return max(abs(target_x - actor_x), abs(target_y - actor_y))


def _obstacle_blocks_movement(obstacle: Dict[str, Any]) -> bool:
    obstacle_type = str(obstacle.get("type", "")).strip().lower()
    if obstacle_type == "door":
        return not bool(obstacle.get("is_open", False))
    return bool(obstacle.get("blocks_movement", False))


def _obstacle_blocks_los(obstacle: Dict[str, Any]) -> bool:
    obstacle_type = str(obstacle.get("type", "")).strip().lower()
    if obstacle_type == "door":
        return not bool(obstacle.get("is_open", False))
    return bool(obstacle.get("blocks_los", False))


def _rebuild_blocked_movement_tiles(map_data: Dict[str, Any]) -> None:
    if not isinstance(map_data, dict):
        return
    blocked: set[Tuple[int, int]] = set()
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict) or not _obstacle_blocks_movement(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            blocked.add((_coerce_int(raw_coord[0], -9999), _coerce_int(raw_coord[1], -9999)))
    map_data["blocked_movement_tiles"] = [[x, y] for x, y in sorted(blocked)]


def _is_door_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    entity_type = str(entity.get("entity_type", "")).strip().lower()
    name = str(entity.get("name", "")).strip().lower()
    return (
        normalized_id.startswith("door_")
        or entity_type == "door"
        or "门" in name
        or "door" in name
    )


def _is_trap_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    entity_type = str(entity.get("entity_type", "")).strip().lower()
    name = str(entity.get("name", "")).strip().lower()
    return (
        normalized_id.startswith("trap_")
        or entity_type == "trap"
        or "陷阱" in name
        or "trap" in name
    )


def _sync_door_state_to_map(
    *,
    map_data: Dict[str, Any],
    entities: Dict[str, Any],
) -> None:
    if not isinstance(map_data, dict):
        return
    obstacles = map_data.get("obstacles")
    if not isinstance(obstacles, list):
        return
    for obstacle in obstacles:
        if not isinstance(obstacle, dict):
            continue
        if str(obstacle.get("type", "")).strip().lower() != "door":
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            ox = _coerce_int(raw_coord[0], -9999)
            oy = _coerce_int(raw_coord[1], -9999)
            for entity_id, entity in entities.items():
                if not isinstance(entity, dict) or not _is_door_entity(str(entity_id), entity):
                    continue
                ex = _coerce_int(entity.get("x"), -9999)
                ey = _coerce_int(entity.get("y"), -9999)
                if ex != ox or ey != oy:
                    continue
                is_open = bool(entity.get("is_open", False))
                obstacle["is_open"] = is_open
                obstacle["blocks_movement"] = not is_open
                obstacle["blocks_los"] = not is_open
                break
    _rebuild_blocked_movement_tiles(map_data)


def _find_transition_zone_at(
    *,
    map_data: Dict[str, Any],
    x: int,
    y: int,
) -> Optional[Dict[str, Any]]:
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        if str(obstacle.get("type", "")).strip().lower() != "transition_zone":
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            ox = _coerce_int(raw_coord[0], -9999)
            oy = _coerce_int(raw_coord[1], -9999)
            if ox == x and oy == y:
                return obstacle
    return None


def _build_environment_objects_for_map(map_data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(map_data.get("environment_objects"), dict) and map_data.get("environment_objects"):
        return copy.deepcopy(map_data.get("environment_objects") or {})
    return {}


def _inject_map_entities_from_obstacles(
    *,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
) -> None:
    if not isinstance(entities, dict) or not isinstance(map_data, dict):
        return
    door_index = 1
    barrel_index = 1
    trap_index = 1
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        obstacle_type = str(obstacle.get("type", "")).strip().lower()
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            x = _coerce_int(raw_coord[0], 0)
            y = _coerce_int(raw_coord[1], 0)
            if obstacle_type == "door":
                is_open = bool(obstacle.get("is_open", False))
                entity_id = (
                    str(obstacle.get("entity_id") or f"door_{door_index}").strip().lower()
                    or f"door_{door_index}"
                )
                door_index += 1
                entities[entity_id] = {
                    "name": str(obstacle.get("name") or "沉重的橡木门"),
                    "entity_type": "door",
                    "faction": "neutral",
                    "hp": 10,
                    "max_hp": 10,
                    "ac": 10,
                    "status": "open" if is_open else "closed",
                    "is_open": is_open,
                    "inventory": {},
                    "equipment": {"main_hand": None, "ranged": None, "armor": None},
                    "position": "camp_center",
                    "x": x,
                    "y": y,
                    "active_buffs": [],
                    "status_effects": [],
                    "affection": 0,
                }
            elif obstacle_type == "powder_barrel":
                entity_id = f"powder_barrel_{barrel_index}"
                barrel_index += 1
                barrel_hp = max(1, _coerce_int(obstacle.get("hp"), 10))
                entities[entity_id] = {
                    "name": "火药桶",
                    "entity_type": "powder_barrel",
                    "faction": "neutral",
                    "hp": barrel_hp,
                    "max_hp": barrel_hp,
                    "ac": 10,
                    "status": "alive",
                    "inventory": {},
                    "equipment": {"main_hand": None, "ranged": None, "armor": None},
                    "position": "camp_center",
                    "x": x,
                    "y": y,
                    "active_buffs": [],
                    "status_effects": [],
                    "affection": 0,
                }
            elif obstacle_type == "trap":
                entity_id = (
                    str(obstacle.get("entity_id") or f"trap_{trap_index}").strip().lower()
                    or f"trap_{trap_index}"
                )
                trap_index += 1
                entities[entity_id] = {
                    "name": str(obstacle.get("name") or "绊线陷阱"),
                    "entity_type": "trap",
                    "faction": "neutral",
                    "hp": 1,
                    "max_hp": 1,
                    "ac": 10,
                    "status": "armed",
                    "is_hidden": bool(obstacle.get("is_hidden", True)),
                    "detect_dc": _coerce_int(obstacle.get("detect_dc"), 13),
                    "disarm_dc": _coerce_int(obstacle.get("disarm_dc"), 15),
                    "save_dc": _coerce_int(obstacle.get("save_dc"), 13),
                    "damage": str(obstacle.get("damage") or "2d6"),
                    "damage_type": str(obstacle.get("damage_type") or "poison").strip().lower(),
                    "trigger_radius": max(0, _coerce_int(obstacle.get("trigger_radius"), 0)),
                    "inventory": {},
                    "equipment": {"main_hand": None, "ranged": None, "armor": None},
                    "position": "camp_center",
                    "x": x,
                    "y": y,
                    "active_buffs": [],
                    "status_effects": [],
                    "affection": 0,
                }


def _build_party_entities_for_map_transition(
    *,
    entities: Dict[str, Any],
) -> Dict[str, Any]:
    transitioned: Dict[str, Any] = {}
    for entity_id_raw, entity in entities.items():
        entity_id = _normalize_entity_id(entity_id_raw)
        if not isinstance(entity, dict):
            continue
        if not _is_player_side_entity(entity_id, entity):
            continue
        transitioned[entity_id] = copy.deepcopy(entity)
    if "player" not in transitioned:
        transitioned["player"] = _build_player_combatant()
    return transitioned


def _pick_party_spawn_tiles(
    *,
    party_ids: List[str],
    map_data: Dict[str, Any],
    spawn_x: int,
    spawn_y: int,
) -> Dict[str, Tuple[int, int]]:
    width = _coerce_int(map_data.get("width"), 0)
    height = _coerce_int(map_data.get("height"), 0)
    blocked = _collect_blocked_movement_tiles(map_data)
    placements: Dict[str, Tuple[int, int]] = {}
    occupied: set[Tuple[int, int]] = set()

    offsets: List[Tuple[int, int]] = [
        (0, 0),
        (1, 0), (-1, 0), (0, 1), (0, -1),
        (1, 1), (-1, 1), (1, -1), (-1, -1),
        (2, 0), (-2, 0), (0, 2), (0, -2),
        (2, 1), (2, -1), (-2, 1), (-2, -1),
        (1, 2), (-1, 2), (1, -2), (-1, -2),
    ]

    for idx, party_id in enumerate(party_ids):
        chosen: Optional[Tuple[int, int]] = None
        start_offset = idx % len(offsets) if offsets else 0
        candidate_offsets = offsets[start_offset:] + offsets[:start_offset]
        for dx, dy in candidate_offsets:
            cx = spawn_x + dx
            cy = spawn_y + dy
            if width > 0 and (cx < 0 or cx >= width):
                continue
            if height > 0 and (cy < 0 or cy >= height):
                continue
            if (cx, cy) in blocked or (cx, cy) in occupied:
                continue
            chosen = (cx, cy)
            break
        if chosen is None:
            chosen = (spawn_x, spawn_y)
        placements[party_id] = chosen
        occupied.add(chosen)
    return placements


def _ordered_party_ids_for_transition(entities: Dict[str, Any]) -> List[str]:
    def _sort_key(entity_id: str) -> Tuple[int, str]:
        normalized_id = _normalize_entity_id(entity_id)
        if normalized_id == "player":
            return (0, normalized_id)
        if normalized_id == "analyst":
            return (1, normalized_id)
        if normalized_id == "scout":
            return (2, normalized_id)
        if normalized_id == "tactician":
            return (3, normalized_id)
        return (4, normalized_id)

    normalized_ids = []
    for entity_id in entities.keys():
        normalized_id = _normalize_entity_id(entity_id)
        if normalized_id:
            normalized_ids.append(normalized_id)
    return sorted(normalized_ids, key=_sort_key)


def _execute_map_transition(
    *,
    entities: Dict[str, Any],
    transition_zone: Dict[str, Any],
) -> Dict[str, Any]:
    target_map_id = _normalize_entity_id(transition_zone.get("target_map"))
    if not target_map_id:
        return {}

    next_map_data = get_map_data(target_map_id)
    if not isinstance(next_map_data, dict) or not next_map_data:
        return {}

    spawn_x = _coerce_int(transition_zone.get("spawn_x"), 0)
    spawn_y = _coerce_int(transition_zone.get("spawn_y"), 0)
    transitioned_entities = _build_party_entities_for_map_transition(entities=entities)
    ordered_party_ids = _ordered_party_ids_for_transition(transitioned_entities)
    placements = _pick_party_spawn_tiles(
        party_ids=ordered_party_ids,
        map_data=next_map_data,
        spawn_x=spawn_x,
        spawn_y=spawn_y,
    )
    map_name = str(next_map_data.get("name") or target_map_id)

    for party_id in ordered_party_ids:
        member = transitioned_entities.get(party_id)
        if not isinstance(member, dict):
            continue
        px, py = placements.get(party_id, (spawn_x, spawn_y))
        member["x"] = px
        member["y"] = py
        member["position"] = f"{map_name} 入口"

    _inject_map_entities_from_obstacles(
        entities=transitioned_entities,
        map_data=next_map_data,
    )
    _sync_door_state_to_map(map_data=next_map_data, entities=transitioned_entities)

    return {
        "entities": transitioned_entities,
        "environment_objects": _build_environment_objects_for_map(next_map_data),
        "map_data": next_map_data,
        "current_location": map_name,
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "turn_resources": {},
        "recent_barks": [],
        "journal_events": [f"🌍 [地图探索] 队伍进入了 {map_name}..."],
    }


def _collect_blocked_movement_tiles(map_data: Dict[str, Any]) -> set[Tuple[int, int]]:
    blocked: set[Tuple[int, int]] = set()
    for raw_tile in map_data.get("blocked_movement_tiles", []) or []:
        if isinstance(raw_tile, (list, tuple)) and len(raw_tile) == 2:
            blocked.add((_coerce_int(raw_tile[0], -9999), _coerce_int(raw_tile[1], -9999)))
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        if not _obstacle_blocks_movement(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if isinstance(raw_coord, (list, tuple)) and len(raw_coord) == 2:
                blocked.add((_coerce_int(raw_coord[0], -9999), _coerce_int(raw_coord[1], -9999)))
    return blocked


def _find_blocking_obstacle_name(map_data: Dict[str, Any], x: int, y: int) -> str:
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        if not _obstacle_blocks_movement(obstacle):
            continue
        obstacle_type = str(obstacle.get("type", "obstacle")).strip().lower()
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            ox = _coerce_int(raw_coord[0], -9999)
            oy = _coerce_int(raw_coord[1], -9999)
            if ox == x and oy == y:
                if obstacle_type == "door":
                    return str(obstacle.get("name") or "门")
                return OBSTACLE_TYPE_DISPLAY.get(obstacle_type, "障碍物")
    return "障碍物"


def _obstacles_at_coordinate(map_data: Dict[str, Any], x: int, y: int) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            ox = _coerce_int(raw_coord[0], -9999)
            oy = _coerce_int(raw_coord[1], -9999)
            if ox == x and oy == y:
                matched.append(obstacle)
                break
    return matched


def _is_campfire_tile(map_data: Dict[str, Any], x: int, y: int) -> bool:
    for obstacle in _obstacles_at_coordinate(map_data, x, y):
        obstacle_type = str(obstacle.get("type", "")).strip().lower()
        if obstacle_type == "campfire":
            return True
    return False


def _validate_move_destination(
    *,
    state: Any,
    entities: Dict[str, Any],
    actor_id: str,
    destination_x: int,
    destination_y: int,
    map_data_override: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    map_data = map_data_override if isinstance(map_data_override, dict) else (state.get("map_data") or {})
    if isinstance(map_data, dict) and map_data:
        width = _coerce_int(map_data.get("width"), 0)
        height = _coerce_int(map_data.get("height"), 0)
        if width > 0 and height > 0:
            if destination_x < 0 or destination_x >= width or destination_y < 0 or destination_y >= height:
                return "移动失败：超出地图边界。"

        blocked_tiles = _collect_blocked_movement_tiles(map_data)
        if (destination_x, destination_y) in blocked_tiles:
            obstacle_name = _find_blocking_obstacle_name(map_data, destination_x, destination_y)
            return f"移动失败：目标位置被{obstacle_name}阻挡。"

    for entity_id, entity in entities.items():
        normalized_entity_id = _normalize_entity_id(entity_id)
        if normalized_entity_id == actor_id:
            continue
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        if _is_door_entity(normalized_entity_id, entity):
            continue
        if _is_trap_entity(normalized_entity_id, entity):
            continue
        entity_x = _coerce_int(entity.get("x"), 4)
        entity_y = _coerce_int(entity.get("y"), 8)
        if entity_x == destination_x and entity_y == destination_y:
            entity_name = _display_entity_name(entity, normalized_entity_id)
            return f"移动失败：目标位置已被 {entity_name} 占据。"
    return None


def _sign(value: int) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _move_toward_target_with_range(
    *,
    actor_x: int,
    actor_y: int,
    target_x: int,
    target_y: int,
    desired_range: int,
) -> tuple[int, int]:
    desired_range = max(1, int(desired_range or 1))
    if _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=target_x,
        target_y=target_y,
    ) <= desired_range:
        return actor_x, actor_y

    dx = target_x - actor_x
    dy = target_y - actor_y
    new_x = actor_x
    new_y = actor_y
    if abs(dx) > desired_range:
        new_x = target_x - (_sign(dx) * desired_range)
    if abs(dy) > desired_range:
        new_y = target_y - (_sign(dy) * desired_range)
    return new_x, new_y


def _movement_budget_from_speed(entity: Dict[str, Any]) -> int:
    speed = _coerce_int(entity.get("speed"), 30)
    # Character YAML follows 5e feet; the tactical grid consumes 5 feet per tile.
    return max(1, speed // 5)


def _move_toward_target_with_range_and_budget(
    *,
    actor_x: int,
    actor_y: int,
    target_x: int,
    target_y: int,
    desired_range: int,
    max_steps: int,
) -> tuple[int, int]:
    desired_range = max(1, int(desired_range or 1))
    max_steps = max(0, int(max_steps or 0))
    new_x = actor_x
    new_y = actor_y

    for _ in range(max_steps):
        if _chebyshev_distance(
            actor_x=new_x,
            actor_y=new_y,
            target_x=target_x,
            target_y=target_y,
        ) <= desired_range:
            break

        dx = target_x - new_x
        dy = target_y - new_y
        if abs(dx) > desired_range:
            new_x += _sign(dx)
        if abs(dy) > desired_range:
            new_y += _sign(dy)

    return new_x, new_y


def _auto_approach_actor_to_target(
    *,
    entities: Dict[str, Any],
    state: Any,
    actor_id: str,
    target: Dict[str, Any],
    target_name: str,
    desired_range: int = 1,
    journal_template: str = "🚶 [自动寻路] {actor_name} 走向了 {target_name}。",
) -> List[str]:
    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    target_x = _coerce_int(target.get("x"), actor_x)
    target_y = _coerce_int(target.get("y"), actor_y)

    if _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=target_x,
        target_y=target_y,
    ) <= max(1, int(desired_range or 1)):
        return []

    new_x, new_y = _move_toward_target_with_range(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=target_x,
        target_y=target_y,
        desired_range=desired_range,
    )
    actor["x"] = new_x
    actor["y"] = new_y
    actor["position"] = f"靠近 {target_name}"
    actor_name = _display_entity_name(actor, actor_id)
    return [journal_template.format(actor_name=actor_name, target_name=target_name)]


def _resolve_item_id_from_context(intent_context: Dict[str, Any]) -> str:
    return str(
        intent_context.get("item_id")
        or intent_context.get("target_item")
        or intent_context.get("action_target")
        or ""
    ).strip().lower()


def _get_equipment(entity: Dict[str, Any]) -> Dict[str, Any]:
    equipment = entity.setdefault("equipment", dict(EMPTY_EQUIPMENT))
    if not isinstance(equipment, dict):
        equipment = dict(EMPTY_EQUIPMENT)
        entity["equipment"] = equipment
    legacy_weapon = equipment.pop("weapon", None)
    if legacy_weapon and not equipment.get("main_hand"):
        equipment["main_hand"] = legacy_weapon
    equipment.setdefault("main_hand", None)
    equipment.setdefault("ranged", None)
    equipment.setdefault("armor", None)
    return equipment


def _resolve_inventory_for_actor(
    *,
    actor_id: str,
    entities: Dict[str, Any],
    player_inventory: Dict[str, int],
) -> tuple[Dict[str, int], str]:
    if actor_id == "player":
        return player_inventory, "玩家"

    actor = entities.get(actor_id)
    if not isinstance(actor, dict):
        return player_inventory, "玩家"

    actor_inventory = actor.setdefault("inventory", {})
    if not isinstance(actor_inventory, dict):
        actor_inventory = {}
        actor["inventory"] = actor_inventory
    return actor_inventory, _display_entity_name(actor, actor_id)


def _equipment_slot_for_item(item_data: Dict[str, Any]) -> str:
    equip_slot = str(item_data.get("equip_slot", "")).strip().lower()
    if equip_slot in {"main_hand", "ranged", "armor"}:
        return equip_slot
    item_type = str(item_data.get("type", "")).strip().lower()
    if item_type == "weapon":
        return "main_hand"
    if item_type == "armor":
        return "armor"
    return ""


def _get_weapon_profile(attacker: Dict[str, Any], preferred_slot: str = "") -> Dict[str, Any]:
    equipment = _get_equipment(attacker)
    slot_order = ["main_hand", "ranged"]
    normalized_preferred_slot = str(preferred_slot or "").strip().lower()
    if normalized_preferred_slot in {"main_hand", "ranged"}:
        slot_order = [normalized_preferred_slot] + [slot for slot in slot_order if slot != normalized_preferred_slot]

    weapon_slot = "main_hand"
    weapon_id = ""
    for slot in slot_order:
        candidate_id = str(equipment.get(slot) or "").strip().lower()
        if candidate_id:
            weapon_slot = slot
            weapon_id = candidate_id
            break
    if not weapon_id:
        return {
            "id": "unarmed",
            "name": "徒手打击",
            "damage_dice": "1d4",
            "damage_bonus": 0,
            "range": 1,
            "slot": "unarmed",
            "weapon_type": "melee",
            "damage_type": "bludgeoning",
            "ability": "STR",
        }

    item_data = get_registry().get(weapon_id)
    weapon_range = _coerce_int(item_data.get("range"), 1)
    weapon_type = str(item_data.get("weapon_type") or "").strip().lower()
    if weapon_type not in {"melee", "ranged"}:
        weapon_type = "ranged" if weapon_slot == "ranged" or weapon_range > 1 else "melee"
    ability_name = "DEX" if weapon_type == "ranged" else "STR"
    return {
        "id": weapon_id,
        "name": get_registry().get_name(weapon_id),
        "damage_dice": str(item_data.get("damage_dice") or item_data.get("damage") or "1d4"),
        "damage_bonus": _coerce_int(item_data.get("damage_bonus"), 0),
        "range": weapon_range,
        "slot": weapon_slot,
        "weapon_type": weapon_type,
        "damage_type": str(item_data.get("damage_type") or "").strip().lower(),
        "ability": ability_name,
    }


def _wants_ranged_attack(user_input: str, intent_context: Dict[str, Any]) -> bool:
    raw_text = f"{user_input or ''} {intent_context.get('reason', '')}"
    lowered = str(raw_text).lower()
    if any(keyword in lowered for keyword in RANGED_ATTACK_HINTS):
        return True

    attack_mode = str(intent_context.get("attack_mode") or intent_context.get("weapon_type") or "").strip().lower()
    return attack_mode == "ranged"


def _select_attack_weapon_profile(
    *,
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    prefer_ranged: bool = False,
) -> Dict[str, Any]:
    equipment = _get_equipment(attacker)
    has_main_hand = bool(str(equipment.get("main_hand") or "").strip())
    has_ranged = bool(str(equipment.get("ranged") or "").strip())

    attacker_x = _coerce_int(attacker.get("x"), 4)
    attacker_y = _coerce_int(attacker.get("y"), 9)
    defender_x = _coerce_int(defender.get("x"), attacker_x)
    defender_y = _coerce_int(defender.get("y"), attacker_y)
    distance = _chebyshev_distance(
        actor_x=attacker_x,
        actor_y=attacker_y,
        target_x=defender_x,
        target_y=defender_y,
    )

    if prefer_ranged and has_ranged:
        return _get_weapon_profile(attacker, preferred_slot="ranged")
    if distance > 1 and has_ranged:
        return _get_weapon_profile(attacker, preferred_slot="ranged")
    if has_main_hand:
        return _get_weapon_profile(attacker, preferred_slot="main_hand")
    if has_ranged:
        return _get_weapon_profile(attacker, preferred_slot="ranged")
    return _get_weapon_profile(attacker)


def _get_spell_profile(intent_context: Dict[str, Any]) -> Dict[str, Any]:
    spell_ref = (
        intent_context.get("spell_id")
        or intent_context.get("item_id")
        or intent_context.get("action_spell")
        or intent_context.get("action_target")
        or ""
    )
    spell_id = resolve_spell_id(spell_ref)
    spell_data = get_spell_data(spell_id)
    if not spell_data:
        return {}
    level = max(0, _coerce_int(spell_data.get("level"), _coerce_int(spell_data.get("slot_level_cost"), 0)))
    return {
        "id": spell_id,
        "name": str(spell_data.get("name") or spell_id),
        "level": level,
        "target_type": str(spell_data.get("target") or spell_data.get("target_type") or "single").strip().lower(),
        "range": max(1, _coerce_int(spell_data.get("range"), 1)),
        "damage_dice": str(spell_data.get("damage") or spell_data.get("damage_dice") or "1d4"),
        "damage_type": str(spell_data.get("damage_type") or "force").strip().lower(),
        "save_ability": str(spell_data.get("saving_throw") or spell_data.get("save_ability") or "DEX").strip().upper(),
        "slot_level_cost": level,
        "aoe": str(spell_data.get("aoe_shape") or spell_data.get("aoe") or ""),
    }


def _ability_display_name(ability_name: str) -> str:
    return {
        "STR": "力量",
        "DEX": "敏捷",
        "CON": "体质",
        "INT": "智力",
        "WIS": "感知",
        "CHA": "魅力",
    }.get(str(ability_name or "").upper(), str(ability_name or "属性"))


def _damage_type_display_name(damage_type: str) -> str:
    return {
        "thunder": "雷鸣",
        "radiant": "光耀",
        "fire": "火焰",
        "cold": "寒冷",
        "force": "力场",
    }.get(str(damage_type or "").strip().lower(), "魔法")


def _normalize_spell_slots(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, int] = {}
    for key, slot_value in value.items():
        slot_key = str(key or "").strip().lower()
        if not slot_key:
            continue
        normalized[slot_key] = max(0, _coerce_int(slot_value, 0))
    return normalized


def _is_consumable_item(item_id: str, item_data: Dict[str, Any]) -> bool:
    if item_data.get("equip_slot"):
        return False
    if item_data.get("is_consumable") is True:
        return True
    if item_data.get("consumable") is True:
        return True
    return str(item_data.get("type", "")).strip().lower() == "consumable"


def _is_alive_entity(entity: Dict[str, Any]) -> bool:
    if not isinstance(entity, dict):
        return False
    if str(entity.get("status", "alive")).strip().lower() in {"dead", "downed", "unconscious"}:
        return False
    return _coerce_int(entity.get("hp"), 1) > 0


def _is_in_combat_state(state: Any) -> bool:
    if bool(state.get("combat_active", False)):
        return True
    return str(state.get("combat_phase", "")).strip().upper() == "IN_COMBAT"


def _iter_living_player_side_entities(entities: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    members: List[Tuple[str, Dict[str, Any]]] = []
    if not isinstance(entities, dict):
        return members
    for entity_id_raw, entity in entities.items():
        entity_id = _normalize_entity_id(entity_id_raw)
        if not isinstance(entity, dict):
            continue
        if not _is_alive_entity(entity):
            continue
        if not _is_player_side_entity(entity_id, entity):
            continue
        members.append((entity_id, entity))
    return members


def _max_spell_slots_for_entity(actor_id: str, entity: Dict[str, Any]) -> Dict[str, int]:
    explicit_caps = (
        entity.get("max_spell_slots")
        or entity.get("spell_slots_max")
        or entity.get("spell_slots_cap")
    )
    normalized_caps = _normalize_spell_slots(explicit_caps)
    if normalized_caps:
        return normalized_caps

    # 实体上的 spell_slots 默认视为该角色的静态上限。
    entity_slots = _normalize_spell_slots(entity.get("spell_slots"))
    if entity_slots:
        return entity_slots

    return _default_spell_slots(actor_id, entity)


def _build_rest_reject_in_combat(
    *,
    state: Any,
    entities: Dict[str, Any],
    intent: str,
) -> Dict[str, Any]:
    return {
        "journal_events": ["❌ [休息] CANNOT_REST_IN_COMBAT：战斗中无法休息。"],
        "entities": entities,
        "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
        "combat_active": bool(state.get("combat_active", False)),
        "initiative_order": list(state.get("initiative_order") or []),
        "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
        "turn_resources": copy.deepcopy(state.get("turn_resources") or {}),
        "raw_roll_data": _build_action_result(
            intent=intent,
            actor=_normalize_entity_id((state.get("intent_context") or {}).get("action_actor", "player")) or "player",
            target="",
            is_success=False,
            result_type="CANNOT_REST_IN_COMBAT",
        ),
        "turn_locked": True,
    }


def _is_powder_barrel(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    entity_type = str(entity.get("entity_type", "")).strip().lower()
    name = str(entity.get("name", "")).strip().lower()
    return (
        normalized_id.startswith("powder_barrel")
        or entity_type == "powder_barrel"
        or "火药桶" in name
        or "powder barrel" in name
    )


def _is_fire_trigger_damage(*, damage_type: str, source: str) -> bool:
    normalized_damage_type = str(damage_type or "").strip().lower()
    normalized_source = str(source or "").strip().lower()
    if normalized_damage_type == "fire":
        return True
    # Sacred Flame acts as ignition source in this project design.
    return normalized_source in {"sacred_flame", "healing_word_fire", "explosion", "campfire"}


def _remove_powder_barrel_obstacle_from_map(
    *,
    map_data: Dict[str, Any],
    x: int,
    y: int,
) -> None:
    if not isinstance(map_data, dict):
        return
    blocked_tiles = map_data.get("blocked_movement_tiles")
    if isinstance(blocked_tiles, list):
        map_data["blocked_movement_tiles"] = [
            tile
            for tile in blocked_tiles
            if not (
                isinstance(tile, (list, tuple))
                and len(tile) == 2
                and _coerce_int(tile[0], -9999) == x
                and _coerce_int(tile[1], -9999) == y
            )
        ]

    obstacles = map_data.get("obstacles")
    if not isinstance(obstacles, list):
        return
    for obstacle in list(obstacles):
        if not isinstance(obstacle, dict):
            continue
        if str(obstacle.get("type", "")).strip().lower() != "powder_barrel":
            continue
        coordinates = obstacle.get("coordinates")
        if not isinstance(coordinates, list):
            continue
        obstacle["coordinates"] = [
            coord
            for coord in coordinates
            if not (
                isinstance(coord, (list, tuple))
                and len(coord) == 2
                and _coerce_int(coord[0], -9999) == x
                and _coerce_int(coord[1], -9999) == y
            )
        ]
    map_data["obstacles"] = [
        obstacle
        for obstacle in obstacles
        if not (
            isinstance(obstacle, dict)
            and str(obstacle.get("type", "")).strip().lower() == "powder_barrel"
            and not (obstacle.get("coordinates") or [])
        )
    ]


def _apply_damage_to_entity(
    *,
    target_id: str,
    target: Dict[str, Any],
    damage: int,
) -> Dict[str, int]:
    normalized_damage = max(0, int(damage or 0))
    current_hp = _coerce_int(target.get("hp"), 0)
    max_hp = _coerce_int(target.get("max_hp"), current_hp)
    new_hp = max(0, current_hp - normalized_damage)
    target["hp"] = new_hp
    target["max_hp"] = max_hp
    target["status"] = "dead" if new_hp <= 0 else "alive"
    return {"old_hp": current_hp, "new_hp": new_hp, "damage": normalized_damage}


def trigger_explosion(
    *,
    center_x: int,
    center_y: int,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
    exploded_coords: Optional[set[Tuple[int, int]]] = None,
) -> List[str]:
    exploded = exploded_coords if exploded_coords is not None else set()
    if (center_x, center_y) in exploded:
        return []
    exploded.add((center_x, center_y))
    _remove_powder_barrel_obstacle_from_map(map_data=map_data, x=center_x, y=center_y)

    logs = [
        f"💥 [地形连锁] ({center_x},{center_y}) 的火药桶被引爆了！剧烈的爆炸吞噬了 3x3 的区域..."
    ]

    for target_id_raw, target in entities.items():
        target_id = _normalize_entity_id(target_id_raw)
        if not isinstance(target, dict) or not _is_alive_entity(target):
            continue
        target_x = _coerce_int(target.get("x"), center_x)
        target_y = _coerce_int(target.get("y"), center_y)
        if max(abs(target_x - center_x), abs(target_y - center_y)) > 1:
            continue

        dex_mod = calculate_ability_modifier(_get_ability_score(target, "DEX", 10))
        save_roll = random.randint(1, 20)
        save_total = save_roll + dex_mod
        save_success = save_total >= 13
        damage_roll = parse_dice_string("3d6")
        applied_damage = damage_roll // 2 if save_success else damage_roll
        damage_payload = _apply_damage_to_entity(
            target_id=target_id,
            target=target,
            damage=applied_damage,
        )
        target_name = _display_entity_name(target, target_id)
        outcome = "成功" if save_success else "失败"
        logs.append(
            f"🔥 [爆炸冲击] {target_name} 进行敏捷豁免 (1d20{dex_mod:+d}={save_total} vs DC 13)，{outcome}！"
            f"受到了 {applied_damage} 点火焰伤害。"
        )
        if damage_payload["new_hp"] <= 0:
            logs.append(f"☠️ [战斗结果] {target_name} 倒下了。")

        if _is_powder_barrel(target_id, target):
            target["hp"] = 0
            target["status"] = "dead"
            _remove_powder_barrel_obstacle_from_map(map_data=map_data, x=target_x, y=target_y)
            nested_logs = trigger_explosion(
                center_x=target_x,
                center_y=target_y,
                entities=entities,
                map_data=map_data,
                exploded_coords=exploded,
            )
            logs.extend(nested_logs)

    return logs


def _process_post_damage_reaction(
    *,
    target_id: str,
    target: Dict[str, Any],
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
    damage_type: str,
    damage_source: str,
    exploded_coords: Optional[set[Tuple[int, int]]] = None,
) -> List[str]:
    if not _is_powder_barrel(target_id, target):
        return []
    target_x = _coerce_int(target.get("x"), 0)
    target_y = _coerce_int(target.get("y"), 0)
    barrel_broken = _coerce_int(target.get("hp"), 0) <= 0
    barrel_ignited = _is_fire_trigger_damage(damage_type=damage_type, source=damage_source)
    if not barrel_broken and not barrel_ignited:
        return []

    target["hp"] = 0
    target["status"] = "dead"
    _remove_powder_barrel_obstacle_from_map(map_data=map_data, x=target_x, y=target_y)
    return trigger_explosion(
        center_x=target_x,
        center_y=target_y,
        entities=entities,
        map_data=map_data,
        exploded_coords=exploded_coords,
    )


def _is_hostile_entity(entity: Dict[str, Any]) -> bool:
    return str(entity.get("faction", "")).strip().lower() in {"hostile", "enemy"}


def _is_player_side_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    if normalized_id in PLAYER_SIDE_ENTITY_IDS:
        return True
    faction = str(entity.get("faction", "")).strip().lower()
    return bool(faction and faction not in {"hostile", "enemy", "neutral"})


def _is_loot_drop_entity(entity_id: str, entity: Dict[str, Any]) -> bool:
    normalized_id = _normalize_entity_id(entity_id)
    entity_type = str(entity.get("entity_type", "")).strip().lower()
    return entity_type == "loot_drop" or normalized_id.startswith("loot_drop_")


def _next_loot_drop_id(entities: Dict[str, Any]) -> str:
    index = 1
    while f"loot_drop_{index}" in entities:
        index += 1
    return f"loot_drop_{index}"


def _build_loot_items_for_entity(entity_id: str, entity: Dict[str, Any]) -> Dict[str, int]:
    normalized_id = _normalize_entity_id(entity_id)
    enemy_type = str(entity.get("enemy_type", "")).strip().lower()
    source_inventory = entity.get("inventory") or {}
    loot_items: Dict[str, int] = {}
    if isinstance(source_inventory, dict):
        for item_id, count in source_inventory.items():
            if not item_id:
                continue
            qty = _coerce_int(count, 0)
            if qty > 0:
                loot_items[str(item_id)] = qty

    # 指定兵种掉落：训练无人机弓箭手必掉短弓 + 1d10 金币。
    if normalized_id == "drone_sentinel" or enemy_type == "archer":
        loot_items["shortbow"] = max(1, _coerce_int(loot_items.get("shortbow"), 1))
        loot_items["gold_coin"] = max(1, random.randint(1, 10))

    return loot_items


def _materialize_loot_drops(entities: Dict[str, Any]) -> List[str]:
    """
    将首次死亡的敌方实体转换为地面战利品实体（loot_drop_*）。
    """
    logs: List[str] = []
    for entity_id_raw, entity in list(entities.items()):
        entity_id = _normalize_entity_id(entity_id_raw)
        if not isinstance(entity, dict):
            continue
        if _is_loot_drop_entity(entity_id, entity) or _is_powder_barrel(entity_id, entity):
            continue
        if not _is_hostile_entity(entity):
            continue
        if _is_alive_entity(entity):
            continue
        if bool(entity.get("loot_generated", False)):
            continue

        loot_items = _build_loot_items_for_entity(entity_id, entity)
        entity["loot_generated"] = True
        entity["inventory"] = {}
        if not loot_items:
            continue

        drop_id = _next_loot_drop_id(entities)
        source_name = _display_entity_name(entity, entity_id)
        drop_x = _coerce_int(entity.get("x"), 0)
        drop_y = _coerce_int(entity.get("y"), 0)
        entities[drop_id] = {
            "name": f"{source_name} 的遗骸",
            "entity_type": "loot_drop",
            "source_name": source_name,
            "source_entity_id": entity_id,
            "faction": "neutral",
            "hp": 0,
            "max_hp": 0,
            "ac": 0,
            "status": "open",
            "inventory": loot_items,
            "equipment": {"main_hand": None, "ranged": None, "armor": None},
            "position": f"地面 ({drop_x},{drop_y})",
            "x": drop_x,
            "y": drop_y,
            "active_buffs": [],
            "status_effects": [],
            "affection": 0,
        }
        logs.append(f"💰 [掉落] {source_name} 倒下后掉落了可搜刮战利品。")

    return logs


def _combatant_ids(entities: Dict[str, Any]) -> List[str]:
    combatants: List[str] = []
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        normalized_id = _normalize_entity_id(entity_id)
        if _is_hostile_entity(entity) or _is_player_side_entity(normalized_id, entity):
            combatants.append(normalized_id)
    return combatants


def _get_ability_score(entity: Dict[str, Any], ability_name: str, default: int = 10) -> int:
    ability_scores = entity.get("ability_scores") or {}
    if isinstance(ability_scores, dict):
        for key, value in ability_scores.items():
            if str(key).strip().upper() == ability_name.upper():
                return _coerce_int(value, default)
    return _coerce_int(entity.get(ability_name.lower()), default)


def _roll_initiative(entities: Dict[str, Any]) -> tuple[List[str], List[Dict[str, Any]], str]:
    entries: List[Dict[str, Any]] = []
    for entity_id in _combatant_ids(entities):
        entity = entities.get(entity_id) or {}
        dex_mod = calculate_ability_modifier(_get_ability_score(entity, "DEX", 10))
        raw_roll = random.randint(1, 20)
        total = raw_roll + dex_mod
        entries.append(
            {
                "id": entity_id,
                "name": _display_entity_name(entity, entity_id),
                "raw_roll": raw_roll,
                "dex_modifier": dex_mod,
                "total": total,
            }
        )

    entries.sort(key=lambda item: item["total"], reverse=True)
    order = [str(item["id"]) for item in entries]
    order_text = ", ".join(f"{item['name']}({item['total']})" for item in entries)
    return order, entries, f"⚔️ 战斗开始！先攻顺序：[{order_text}]"


def _initialize_combat_fields(
    *,
    state: Any,
    entities: Dict[str, Any],
) -> tuple[Dict[str, Any], List[str]]:
    initiative_order, initiative_rolls, initiative_log = _roll_initiative(entities)
    if not initiative_order:
        return {}, []

    current_turn_index = 0
    first_block = _get_active_turn_block(
        state=state,
        entities=entities,
        initiative_order=initiative_order,
        current_turn_index=current_turn_index,
    )
    auto_enemy_turn = False
    if first_block:
        first_entity = entities.get(first_block[0], {})
        if isinstance(first_entity, dict) and _turn_side_key(first_block[0], first_entity) == "hostile":
            auto_enemy_turn = True

    fields = {
        "combat_phase": "IN_COMBAT",
        "combat_active": True,
        "initiative_order": initiative_order,
        "current_turn_index": current_turn_index,
        "initiative_rolls": initiative_rolls,
        "auto_enemy_turn": auto_enemy_turn,
    }
    return fields, [initiative_log]


def _apply_ambush_opening(
    *,
    entities: Dict[str, Any],
    attacker_id: str,
) -> tuple[bool, List[str]]:
    attacker = entities.get(attacker_id)
    if not isinstance(attacker, dict):
        return False, []
    if not _is_player_side_entity(attacker_id, attacker):
        return False, []
    if not _has_status_effect(attacker, "hidden"):
        return False, []

    attacker_name = _display_entity_name(attacker, attacker_id)
    _remove_status_effect(attacker, "hidden")
    logs = [f"🫥 [潜袭] {attacker_name} 发起突袭，潜行状态解除。"]

    surprised_targets: List[str] = []
    for entity_id, entity in entities.items():
        normalized_id = _normalize_entity_id(entity_id)
        if normalized_id == attacker_id:
            continue
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        if not _is_hostile_entity(entity):
            continue
        _add_or_refresh_status_effect(entity, "surprised", 1)
        surprised_targets.append(_display_entity_name(entity, normalized_id))
    if surprised_targets:
        logs.append(f"😱 [突袭] 敌方被打了个措手不及：{', '.join(surprised_targets)} 进入受惊状态。")
    return True, logs


def _evaluate_vision_alert_after_move(
    *,
    state: Any,
    entities: Dict[str, Any],
    actor_id: str,
    map_data: Dict[str, Any],
) -> Dict[str, Any]:
    if _is_in_combat_state(state):
        return {}
    actor = entities.get(actor_id)
    if not isinstance(actor, dict) or not _is_alive_entity(actor):
        return {}
    if not _is_player_side_entity(actor_id, actor):
        return {}

    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    visible_hostiles: List[Tuple[str, Dict[str, Any], str]] = []
    for enemy_id_raw, enemy in entities.items():
        enemy_id = _normalize_entity_id(enemy_id_raw)
        if enemy_id == actor_id:
            continue
        if not isinstance(enemy, dict) or not _is_alive_entity(enemy):
            continue
        if not _is_hostile_entity(enemy):
            continue
        enemy_x = _coerce_int(enemy.get("x"), actor_x)
        enemy_y = _coerce_int(enemy.get("y"), actor_y)
        distance = _chebyshev_distance(
            actor_x=actor_x,
            actor_y=actor_y,
            target_x=enemy_x,
            target_y=enemy_y,
        )
        if distance > 6:
            continue
        if not check_line_of_sight((actor_x, actor_y), (enemy_x, enemy_y), map_data):
            continue
        visible_hostiles.append((enemy_id, enemy, _display_entity_name(enemy, enemy_id)))

    if not visible_hostiles:
        return {}

    actor_name = _display_entity_name(actor, actor_id)
    is_hidden = _has_status_effect(actor, "hidden")
    vision_events: List[str] = []
    spotted_enemy_name = visible_hostiles[0][2]

    if is_hidden:
        dex_mod = calculate_ability_modifier(_get_ability_score(actor, "DEX", 10))
        for enemy_id, enemy, enemy_name in visible_hostiles:
            wis_mod = calculate_ability_modifier(_get_ability_score(enemy, "WIS", 10))
            passive_perception = 10 + wis_mod
            stealth_roll = random.randint(1, 20)
            stealth_total = stealth_roll + dex_mod
            vision_events.append(
                f"👀 [潜行对抗] {actor_name} 试图潜过 {enemy_name} 的警戒线："
                f"{stealth_roll}(+{dex_mod})={stealth_total} vs 被动感知 {passive_perception}。"
            )
            if stealth_total < passive_perception:
                _remove_status_effect(actor, "hidden")
                vision_events.append(f"🚨 [警戒触发] {actor_name} 暴露了位置，被 {enemy_name} 发现！")
                spotted_enemy_name = enemy_name
                break
        else:
            vision_events.append(f"🫥 [潜行] {actor_name} 保持隐匿，未触发战斗。")
            return {"journal_events": vision_events}
    else:
        vision_events.append(f"🚨 [警戒触发] {actor_name} 进入 {spotted_enemy_name} 的视野，战斗爆发！")

    combat_fields, combat_events = _initialize_combat_fields(state=state, entities=entities)
    return {
        **combat_fields,
        "journal_events": vision_events + combat_events,
    }


def _remove_trap_obstacle_from_map(
    *,
    map_data: Dict[str, Any],
    x: int,
    y: int,
) -> None:
    if not isinstance(map_data, dict):
        return
    obstacles = map_data.get("obstacles")
    if not isinstance(obstacles, list):
        return

    for obstacle in obstacles:
        if not isinstance(obstacle, dict):
            continue
        if str(obstacle.get("type", "")).strip().lower() != "trap":
            continue
        coordinates = obstacle.get("coordinates")
        if not isinstance(coordinates, list):
            continue
        obstacle["coordinates"] = [
            coord
            for coord in coordinates
            if not (
                isinstance(coord, (list, tuple))
                and len(coord) == 2
                and _coerce_int(coord[0], -9999) == x
                and _coerce_int(coord[1], -9999) == y
            )
        ]
    map_data["obstacles"] = [
        obstacle
        for obstacle in obstacles
        if not (
            isinstance(obstacle, dict)
            and str(obstacle.get("type", "")).strip().lower() == "trap"
            and not (obstacle.get("coordinates") or [])
        )
    ]
    _rebuild_blocked_movement_tiles(map_data)


def _sync_lab_trap_state_to_environment(
    *,
    environment_objects: Dict[str, Any],
    trap_id: str,
    status: str,
    is_hidden: bool,
) -> None:
    trap = environment_objects.get(trap_id)
    if not isinstance(trap, dict):
        return
    trap["status"] = status
    trap["is_hidden"] = is_hidden


def _apply_poisoned_once(entity: Dict[str, Any], *, duration: int = 3) -> bool:
    effects = _ensure_status_effects(entity)
    for effect in effects:
        if str(effect.get("type") or "").strip().lower() == "poisoned":
            effect["duration"] = max(_coerce_int(effect.get("duration"), 0), duration)
            entity["status_effects"] = effects
            return False
    effects.append({"type": "poisoned", "duration": duration})
    entity["status_effects"] = effects
    return True


def _trigger_trap_entity(
    *,
    trap_id: str,
    trap: Dict[str, Any],
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
    trigger_actor_id: str,
    flags: Optional[Dict[str, Any]] = None,
    environment_objects: Optional[Dict[str, Any]] = None,
    affected_actor_ids: Optional[List[str]] = None,
) -> List[str]:
    trap_name = _display_entity_name(trap, trap_id)
    trigger_actor = entities.get(trigger_actor_id, {})
    trigger_actor_name = _display_entity_name(
        trigger_actor if isinstance(trigger_actor, dict) else {},
        trigger_actor_id,
    )
    trap_x = _coerce_int(trap.get("x"), 0)
    trap_y = _coerce_int(trap.get("y"), 0)
    radius = max(0, _coerce_int(trap.get("trigger_radius"), 0))
    save_dc = _coerce_int(trap.get("save_dc"), 13)
    damage_formula = str(trap.get("damage") or "2d6")
    damage_type = str(trap.get("damage_type") or "poison").strip().lower()
    damage_type_name = _damage_type_display_name(damage_type)
    explicit_affected = {
        _normalize_entity_id(actor_id)
        for actor_id in (affected_actor_ids or [])
        if _normalize_entity_id(actor_id)
    }

    logs = [f"💥 [陷阱触发] {trigger_actor_name} 不慎踩中了 {trap_name}！"]
    is_lab_poison_trap = _normalize_entity_id(trap_id) == "gas_trap_1"
    if is_lab_poison_trap:
        logs.append("[毒气陷阱] gas_trap_1 triggered")
    for entity_id, entity in entities.items():
        normalized_id = _normalize_entity_id(entity_id)
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        if normalized_id == trap_id:
            continue
        entity_x = _coerce_int(entity.get("x"), trap_x)
        entity_y = _coerce_int(entity.get("y"), trap_y)
        distance = _chebyshev_distance(
            actor_x=trap_x,
            actor_y=trap_y,
            target_x=entity_x,
            target_y=entity_y,
        )
        is_explicitly_affected = normalized_id in explicit_affected
        if distance > radius and not is_explicitly_affected:
            continue

        if is_lab_poison_trap and _is_player_side_entity(normalized_id, entity):
            if _apply_poisoned_once(entity, duration=3):
                entity_name = _display_entity_name(entity, normalized_id)
                logs.append(f"🤢 [状态] {entity_name} 获得 中毒（3 回合）。")

        dex_mod = calculate_ability_modifier(_get_ability_score(entity, "DEX", 10))
        save_roll = random.randint(1, 20)
        save_total = save_roll + dex_mod
        damage_roll = parse_dice_string(damage_formula)
        is_save_success = save_total >= save_dc
        applied_damage = damage_roll // 2 if is_save_success else damage_roll
        hp_change = _apply_damage_to_entity(
            target_id=normalized_id,
            target=entity,
            damage=applied_damage,
        )
        outcome = "成功" if is_save_success else "失败"
        entity_name = _display_entity_name(entity, normalized_id)
        logs.append(
            f"💥 [陷阱] {entity_name} 进行敏捷豁免 "
            f"(1d20{dex_mod:+d}={save_total} vs DC {save_dc})，{outcome}！"
            f"受到 {hp_change['damage']} 点{damage_type_name}伤害。"
        )
        if hp_change["new_hp"] <= 0:
            logs.append(f"☠️ [战斗结果] {entity_name} 倒下了。")

    trap["status"] = "triggered"
    trap["hp"] = 0
    trap["is_hidden"] = False
    if is_lab_poison_trap and isinstance(flags, dict):
        flags["hazard_lab_poison_trap_triggered"] = True
        _mark_act2_trap_triggered(flags)
    if is_lab_poison_trap and isinstance(environment_objects, dict):
        _sync_lab_trap_state_to_environment(
            environment_objects=environment_objects,
            trap_id=trap_id,
            status="triggered",
            is_hidden=False,
        )
    entities.pop(trap_id, None)
    _remove_trap_obstacle_from_map(map_data=map_data, x=trap_x, y=trap_y)
    return logs


def _new_domain_event_id() -> str:
    return f"evt_{uuid4().hex}"


def _domain_flag_event(*, actor_id: str, turn_index: int, key: str, value: Any = True) -> Dict[str, Any]:
    return event_to_dict(
        DomainEvent(
            event_id=_new_domain_event_id(),
            event_type="world_flag_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"key": key, "value": bool(value)},
        )
    )


def _domain_affection_event(*, actor_id: str, turn_index: int, target_actor_id: str, delta: int, reason: str) -> Dict[str, Any]:
    return event_to_dict(
        DomainEvent(
            event_id=_new_domain_event_id(),
            event_type="actor_affection_changed",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={"target_actor_id": target_actor_id, "delta": int(delta), "reason": reason},
        )
    )


def _domain_item_transfer_event(
    *,
    actor_id: str,
    turn_index: int,
    from_entity: str,
    to_entity: str,
    item_id: str,
    quantity: int,
    reason: str,
) -> Dict[str, Any]:
    return event_to_dict(
        DomainEvent(
            event_id=_new_domain_event_id(),
            event_type="actor_item_transaction_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload={
                "social_action": {
                    "action_type": "item_transfer",
                    "actor_id": actor_id,
                    "target_actor_id": to_entity,
                    "item_id": item_id,
                    "quantity": int(quantity),
                    "accepted": True,
                    "reason": reason,
                },
                "transaction": {
                    "transaction_type": "transfer",
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "item": item_id,
                    "quantity": int(quantity),
                    "accepted": True,
                    "reason": reason,
                },
            },
        )
    )


def _domain_negotiation_event(
    *,
    actor_id: str,
    turn_index: int,
    target_actor_id: str,
    reason: str,
    status_set: str = "",
    faction_set: str = "",
    force_hostile: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "target_actor_id": target_actor_id,
        "reason": reason,
        "force_hostile": force_hostile,
        "trigger_combat": False,
    }
    if status_set:
        payload["status_set"] = status_set
    if faction_set:
        payload["faction_set"] = faction_set
    return event_to_dict(
        DomainEvent(
            event_id=_new_domain_event_id(),
            event_type="actor_negotiation_outcome_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility="party",
            payload=payload,
        )
    )


def _party_actor_ids_in_room(entities: Dict[str, Any]) -> List[str]:
    actor_ids: List[str] = []
    for actor_id in ("player", "scout", "analyst", "tactician"):
        entity = entities.get(actor_id)
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        actor_ids.append(actor_id)
    return actor_ids or ["player"]


def _ensure_poison_valve_object(environment_objects: Dict[str, Any], entities: Dict[str, Any]) -> Dict[str, Any]:
    valve = environment_objects.get("poison_valve")
    if not isinstance(valve, dict):
        valve = entities.get("poison_valve")
    if not isinstance(valve, dict):
        valve = {
            "id": "poison_valve",
            "type": "trap",
            "entity_type": "trap",
            "name": "毒气阀门",
            "status": "armed",
            "is_hidden": False,
            "x": 6,
            "y": 4,
            "room_id": "room_d_lab",
        }
        environment_objects["poison_valve"] = valve
    return valve


def _trigger_act4_poison_valve(
    *,
    entities: Dict[str, Any],
    environment_objects: Dict[str, Any],
    flags: Dict[str, Any],
    trigger_actor_id: str,
) -> List[str]:
    valve = _ensure_poison_valve_object(environment_objects, entities)
    valve["status"] = "triggered"
    valve["is_hidden"] = False
    flags["act4_poison_valve_intact"] = True
    flags["act4_poison_valve_triggered"] = True
    flags["act4_lab_poison_leak"] = True
    logs = ["[毒气泄漏] poison_valve -> lab_poison"]
    if trigger_actor_id == "gatekeeper":
        logs.append("[毒气泄漏] gatekeeper -> poison_valve")
    for actor_id in _party_actor_ids_in_room(entities):
        entity = entities.get(actor_id)
        if not isinstance(entity, dict):
            continue
        if _apply_poisoned_once(entity, duration=3):
            logs.append(f"🤢 [状态] {_display_entity_name(entity, actor_id)} 获得 中毒（3 回合）。")
    return logs


def execute_gatekeeper_boss_resolution_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    flags = dict(state.get("flags") or {})
    map_data = copy.deepcopy(state.get("map_data")) if isinstance(state.get("map_data"), dict) else {}
    intent_context = state.get("intent_context") if isinstance(state.get("intent_context"), dict) else {}
    context = detect_gatekeeper_boss_resolution_context(
        {
            **(state if isinstance(state, dict) else {}),
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
        },
        str(state.get("user_input") or ""),
        intent_context,
    )
    actor_id = _normalize_entity_id(intent_context.get("action_actor") or "player") or "player"
    if not context:
        return {
            "journal_events": [],
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="ACTION",
                actor=actor_id,
                target=str(intent_context.get("action_target") or "gatekeeper"),
                is_success=False,
                result_type="NO_BOSS_CONTEXT",
            ),
        }

    route = str(context.get("route") or "").strip()
    turn_index = int(state.get("turn_count") or 0)
    pending_events: List[Dict[str, Any]] = []
    journal_events: List[str] = []
    force_success = bool(context.get("force_success", False))
    force_failure = bool(context.get("force_failure", False))

    if route == "disarm_poison_valve":
        actor_id = "scout"
        valve = _ensure_poison_valve_object(environment_objects, entities)
        success = not bool(flags.get("hazard_lab_force_poison_valve_disarm_failure", False))
        if success:
            valve["status"] = "disabled"
            flags["act4_poison_valve_disabled"] = True
            flags["act4_lab_poison_leak"] = False
            journal_events.append("[毒气阀门] scout -> disabled")
        else:
            flags["act4_poison_valve_disabled"] = False
            journal_events.append("[毒气阀门失败] scout -> poison_valve")
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="ACTION",
                actor=actor_id,
                target="poison_valve",
                is_success=success,
                result_type="SUCCESS" if success else "FAIL",
                extra={"dc": 14, "modifier": 3},
            ),
        }

    if route == "over_threat":
        gatekeeper = entities.get("gatekeeper")
        if isinstance(gatekeeper, dict):
            gatekeeper["faction"] = "hostile"
        journal_events.extend(_trigger_act4_poison_valve(entities=entities, environment_objects=environment_objects, flags=flags, trigger_actor_id="gatekeeper"))
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(intent="ACTION", actor="player", target="gatekeeper", is_success=False, result_type="OVER_THREAT_POISON"),
        }

    if route == "truth_negotiation":
        actor_id = "player"
        truth_available = bool(context.get("truth_available", False))
        dc = 10 if truth_available else 17
        modifier = 2
        roll = roll_d20(dc=dc, modifier=modifier, roll_type="advantage" if truth_available else "normal")
        success = bool(roll.get("is_success", False))
        if force_success:
            success = True
        if force_failure:
            success = False
        pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_truth_negotiation_available", value=truth_available))
        if success:
            pending_events.extend(
                [
                    _domain_negotiation_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="gatekeeper", reason="act4_truth_negotiation_success", status_set="spared", faction_set="neutralized"),
                    _domain_item_transfer_event(actor_id=actor_id, turn_index=turn_index, from_entity="gatekeeper", to_entity="player", item_id="heavy_iron_key", quantity=1, reason="act4_truth_negotiation_key_surrender"),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_heavy_iron_key_obtained", value=True),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_gatekeeper_spared", value=True),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_negotiation_success", value=True),
                    _domain_affection_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="analyst", delta=1, reason="act4_truth_negotiation"),
                    _domain_affection_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="tactician", delta=-1, reason="act4_truth_negotiation"),
                ]
            )
            journal_events.append("[Boss解决] negotiation -> key_surrendered")
        else:
            pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_negotiation_success", value=False))
            gatekeeper = entities.get("gatekeeper")
            if isinstance(gatekeeper, dict):
                gatekeeper["faction"] = "hostile"
            journal_events.extend(_trigger_act4_poison_valve(entities=entities, environment_objects=environment_objects, flags=flags, trigger_actor_id="gatekeeper"))
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "pending_events": pending_events,
            "raw_roll_data": {"intent": "PERSUASION", "actor": actor_id, "target": "gatekeeper", "dc": dc, "modifier": modifier, "result": {**roll, "is_success": success}},
        }

    if route == "scout_steal":
        actor_id = "scout"
        scout = entities.get("scout") if isinstance(entities.get("scout"), dict) else {}
        dex_mod = calculate_ability_modifier(_get_ability_score(scout, "DEX", 16)) + 2
        dc = 13
        roll = roll_d20(dc=dc, modifier=dex_mod, roll_type="normal")
        success = bool(roll.get("is_success", False))
        if force_success:
            success = True
        if force_failure:
            success = False
        pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_scout_steal_key_attempted", value=True))
        if success:
            pending_events.extend(
                [
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_scout_steal_key_success", value=True),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_heavy_iron_key_obtained", value=True),
                    _domain_item_transfer_event(actor_id=actor_id, turn_index=turn_index, from_entity="gatekeeper", to_entity="player", item_id="heavy_iron_key", quantity=1, reason="act4_scout_steal_key"),
                    _domain_affection_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="scout", delta=1, reason="act4_steal_key"),
                    _domain_negotiation_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="gatekeeper", reason="act4_scout_steal_success", faction_set="hostile"),
                ]
            )
            journal_events.append("[Boss解决] scout_steal -> heavy_iron_key")
        else:
            pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_scout_steal_key_success", value=False))
            gatekeeper = entities.get("gatekeeper")
            if isinstance(gatekeeper, dict):
                gatekeeper["faction"] = "hostile"
            journal_events.append("[偷钥匙失败] scout -> gatekeeper_alerted")
            journal_events.extend(_trigger_act4_poison_valve(entities=entities, environment_objects=environment_objects, flags=flags, trigger_actor_id="gatekeeper"))
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "pending_events": pending_events,
            "raw_roll_data": {"intent": "SLEIGHT_OF_HAND", "actor": actor_id, "target": "gatekeeper", "dc": dc, "modifier": dex_mod, "result": {**roll, "is_success": success}},
        }

    if route == "assault":
        actor_id = "tactician"
        dc = 11
        modifier = 4
        roll = roll_d20(dc=dc, modifier=modifier, roll_type="normal")
        success = bool(roll.get("is_success", False))
        if force_success:
            success = True
        if force_failure:
            success = False
        pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_assault_attempted", value=True))
        if success:
            pending_events.extend(
                [
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_assault_success", value=True),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="world_hazard_lab_gatekeeper_defeated", value=True),
                    _domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_heavy_iron_key_obtained", value=True),
                    _domain_negotiation_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="gatekeeper", reason="act4_assault_success", status_set="dead", faction_set="defeated"),
                    _domain_item_transfer_event(actor_id=actor_id, turn_index=turn_index, from_entity="gatekeeper", to_entity="player", item_id="heavy_iron_key", quantity=1, reason="act4_assault_loot_key"),
                    _domain_affection_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="tactician", delta=1, reason="act4_assault"),
                    _domain_affection_event(actor_id=actor_id, turn_index=turn_index, target_actor_id="analyst", delta=-1, reason="act4_assault"),
                ]
            )
            journal_events.append("[Boss解决] assault -> gatekeeper_defeated")
        else:
            pending_events.append(_domain_flag_event(actor_id=actor_id, turn_index=turn_index, key="act4_assault_success", value=False))
            gatekeeper = entities.get("gatekeeper")
            if isinstance(gatekeeper, dict):
                gatekeeper["faction"] = "hostile"
            journal_events.extend(_trigger_act4_poison_valve(entities=entities, environment_objects=environment_objects, flags=flags, trigger_actor_id="gatekeeper"))
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "flags": flags,
            "map_data": map_data,
            "pending_events": pending_events,
            "raw_roll_data": {"intent": "ATTACK", "actor": actor_id, "target": "gatekeeper", "dc": dc, "modifier": modifier, "result": {**roll, "is_success": success}},
        }

    return {
        "journal_events": [],
        "entities": entities,
        "environment_objects": environment_objects,
        "player_inventory": player_inventory,
        "flags": flags,
        "map_data": map_data,
        "raw_roll_data": _build_action_result(intent="ACTION", actor=actor_id, target="gatekeeper", is_success=False, result_type="UNKNOWN_ROUTE"),
    }


def _evaluate_traps_after_move(
    *,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
    actor_id: str,
    flags: Optional[Dict[str, Any]] = None,
    environment_objects: Optional[Dict[str, Any]] = None,
) -> List[str]:
    normalized_actor_id = _normalize_entity_id(actor_id)
    if normalized_actor_id not in PLAYER_SIDE_ENTITY_IDS:
        return []
    actor = entities.get(actor_id)
    if not isinstance(actor, dict) or not _is_alive_entity(actor):
        return []

    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    passive_perception = 10 + calculate_ability_modifier(_get_ability_score(actor, "WIS", 10))
    actor_name = _display_entity_name(actor, actor_id)
    logs: List[str] = []

    trap_pairs: List[Tuple[str, Dict[str, Any]]] = []
    for entity_id, entity in entities.items():
        normalized_id = _normalize_entity_id(entity_id)
        if not isinstance(entity, dict):
            continue
        if not _is_trap_entity(normalized_id, entity):
            continue
        status = str(entity.get("status", "armed")).strip().lower()
        if status in {"dead", "disabled", "triggered"}:
            continue
        if normalized_id == "gas_trap_1" and isinstance(flags, dict) and (
            bool(flags.get("hazard_lab_poison_trap_disarmed", False))
            or bool(flags.get("hazard_lab_poison_trap_triggered", False))
        ):
            continue
        if normalized_id == "poison_valve" and isinstance(flags, dict) and not (
            bool(flags.get("act4_gatekeeper_confrontation_started", False))
            or bool(flags.get("act4_boss_encounter_started", False))
            or bool(flags.get("act4_boss_room_entered", False))
        ):
            continue
        trap_pairs.append((normalized_id, entity))

    # Step 1: 先判定是否踩中陷阱（trigger_radius=0 表示踩中即触发）。
    for trap_id, trap in list(trap_pairs):
        trap_x = _coerce_int(trap.get("x"), actor_x)
        trap_y = _coerce_int(trap.get("y"), actor_y)
        trigger_radius = max(0, _coerce_int(trap.get("trigger_radius"), 0))
        distance = _chebyshev_distance(
            actor_x=actor_x,
            actor_y=actor_y,
            target_x=trap_x,
            target_y=trap_y,
        )
        if distance > trigger_radius:
            continue
        logs.extend(
            _trigger_trap_entity(
                trap_id=trap_id,
                trap=trap,
                entities=entities,
                map_data=map_data,
                trigger_actor_id=actor_id,
                flags=flags,
                environment_objects=environment_objects,
            )
        )

    # Step 2: 再进行被动感知扫描（半径 3 格）。
    for trap_id, trap in trap_pairs:
        if trap_id not in entities:
            continue
        is_hidden = bool(trap.get("is_hidden", True))
        if not is_hidden:
            continue
        trap_x = _coerce_int(trap.get("x"), actor_x)
        trap_y = _coerce_int(trap.get("y"), actor_y)
        distance = _chebyshev_distance(
            actor_x=actor_x,
            actor_y=actor_y,
            target_x=trap_x,
            target_y=trap_y,
        )
        if distance > 3:
            continue
        detect_dc = _coerce_int(trap.get("detect_dc"), 13)
        if passive_perception >= detect_dc:
            trap["is_hidden"] = False
            trap["status"] = "revealed"
            trap_name = _display_entity_name(trap, trap_id)
            logs.append(f"👁️ [洞察] {actor_name} 察觉到了地上的 {trap_name}！")

    return logs


def _combat_has_live_hostiles(entities: Dict[str, Any]) -> bool:
    return any(
        isinstance(entity, dict) and _is_alive_entity(entity) and _is_hostile_entity(entity)
        for entity in entities.values()
    )


def _combat_has_live_player_side(entities: Dict[str, Any]) -> bool:
    return any(
        isinstance(entity, dict)
        and _is_alive_entity(entity)
        and _is_player_side_entity(str(entity_id), entity)
        for entity_id, entity in entities.items()
    )


def _prune_initiative_order(order: List[str], entities: Dict[str, Any]) -> List[str]:
    pruned: List[str] = []
    for entity_id in order:
        normalized_id = _normalize_entity_id(entity_id)
        entity = entities.get(normalized_id)
        if isinstance(entity, dict) and _is_alive_entity(entity):
            pruned.append(normalized_id)
    return pruned


def _combat_end_event(entities: Dict[str, Any]) -> str:
    if not _combat_has_live_hostiles(entities):
        return "🏁 [战斗结束] 敌对单位已经被肃清。"
    if not _combat_has_live_player_side(entities):
        return "💀 [战斗结束] 队伍已经失去战斗能力。"
    return ""


def _build_out_of_combat_result(
    *,
    action_result: Dict[str, Any],
    entities: Dict[str, Any],
    journal_events: List[str],
    include_victory_banner: bool,
    recent_barks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    end_logs = list(journal_events)
    if include_victory_banner:
        end_logs.append("🏆 战斗结束！所有敌人都被消灭了。进入自由探索模式。")
    end_logs.append(_combat_end_event(entities))
    bark_events = (
        list(recent_barks)
        if isinstance(recent_barks, list)
        else list(action_result.get("recent_barks") or [])
    )
    return {
        **action_result,
        "entities": entities,
        "journal_events": end_logs,
        "recent_barks": bark_events,
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "turn_resources": {},
    }


def _get_active_turn_id(state: Any) -> str:
    initiative_order = state.get("initiative_order") or []
    if not isinstance(initiative_order, list) or not initiative_order:
        return ""
    current_turn_index = _coerce_int(state.get("current_turn_index"), 0)
    if current_turn_index < 0 or current_turn_index >= len(initiative_order):
        current_turn_index = 0
    return _normalize_entity_id(initiative_order[current_turn_index])


def _turn_side_key(entity_id: str, entity: Dict[str, Any]) -> str:
    if _is_hostile_entity(entity):
        return "hostile"
    if _is_player_side_entity(entity_id, entity):
        return "party"
    return "neutral"


def _get_active_turn_block(
    *,
    state: Any,
    entities: Dict[str, Any],
    initiative_order: Optional[List[str]] = None,
    current_turn_index: Optional[int] = None,
) -> List[str]:
    order = initiative_order if initiative_order is not None else list(state.get("initiative_order") or [])
    if not isinstance(order, list) or not order:
        return []
    index = (
        _coerce_int(current_turn_index, 0)
        if current_turn_index is not None
        else _coerce_int(state.get("current_turn_index"), 0)
    )
    if index < 0 or index >= len(order):
        index = 0

    first_id = _normalize_entity_id(order[index])
    first_entity = entities.get(first_id)
    if not isinstance(first_entity, dict) or not _is_alive_entity(first_entity):
        return []
    side_key = _turn_side_key(first_id, first_entity)
    block = [first_id]
    for offset in range(index + 1, len(order)):
        candidate_id = _normalize_entity_id(order[offset])
        candidate = entities.get(candidate_id)
        if not isinstance(candidate, dict) or not _is_alive_entity(candidate):
            break
        if _turn_side_key(candidate_id, candidate) != side_key:
            break
        block.append(candidate_id)
    return block


def _active_block_side(
    *,
    state: Any,
    entities: Dict[str, Any],
    initiative_order: Optional[List[str]] = None,
    current_turn_index: Optional[int] = None,
) -> str:
    block = _get_active_turn_block(
        state=state,
        entities=entities,
        initiative_order=initiative_order,
        current_turn_index=current_turn_index,
    )
    if not block:
        return ""
    entity = entities.get(block[0], {})
    if not isinstance(entity, dict):
        return ""
    return _turn_side_key(block[0], entity)


def _default_spell_slots(actor_id: str, entity: Dict[str, Any]) -> Dict[str, int]:
    existing = entity.get("spell_slots")
    if isinstance(existing, dict):
        normalized: Dict[str, int] = {}
        for key, value in existing.items():
            slot_key = str(key or "").strip().lower()
            if not slot_key:
                continue
            normalized[slot_key] = max(0, _coerce_int(value, 0))
        if normalized:
            return normalized

    fallback = SPELLCASTER_DEFAULT_SLOTS.get(_normalize_entity_id(actor_id), {})
    return {slot: int(value) for slot, value in fallback.items()}


def _default_turn_resources(actor_id: str, entity: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "action": 1,
        "bonus_action": 1,
        "movement": _movement_budget_from_speed(entity),
        "_turn_started": False,
    }
    spell_slots = _default_spell_slots(actor_id, entity)
    if spell_slots:
        payload["spell_slots"] = spell_slots
    return payload


def _ensure_turn_resources_for_block(
    *,
    state: Any,
    entities: Dict[str, Any],
    active_block: List[str],
    force_reset: bool = False,
) -> Dict[str, Dict[str, Any]]:
    existing = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(existing, dict):
        existing = {}
    for actor_id in active_block:
        actor = entities.get(actor_id, {})
        if (
            force_reset
            or actor_id not in existing
            or not isinstance(existing.get(actor_id), dict)
        ):
            existing[actor_id] = _default_turn_resources(
                actor_id,
                actor if isinstance(actor, dict) else {},
            )
    return existing


def _begin_turn_for_block(
    *,
    entities: Dict[str, Any],
    turn_resources: Dict[str, Dict[str, Any]],
    active_block: List[str],
    force_reset: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    updated_resources = copy.deepcopy(turn_resources or {})
    if not isinstance(updated_resources, dict):
        updated_resources = {}

    journal_events: List[str] = []
    for actor_id in active_block:
        actor = entities.get(actor_id, {})
        if not isinstance(actor, dict):
            actor = {}
            entities[actor_id] = actor
        _ensure_status_effects(actor)

        current = updated_resources.get(actor_id)
        reset_actor_turn = force_reset or not isinstance(current, dict)
        if not reset_actor_turn and isinstance(current, dict):
            if (
                int(current.get("action", 0) or 0) <= 0
                and int(current.get("bonus_action", 0) or 0) <= 0
                and int(current.get("movement", 0) or 0) <= 0
            ):
                reset_actor_turn = True

        resources = (
            _default_turn_resources(actor_id, actor)
            if reset_actor_turn
            else dict(current if isinstance(current, dict) else {})
        )
        if not bool(resources.get("_turn_started", False)):
            journal_events.extend(
                _apply_start_of_turn_status_effects(
                    entity_id=actor_id,
                    entity=actor,
                    resources=resources,
                )
            )
            resources["_turn_started"] = True
        updated_resources[actor_id] = resources
    return updated_resources, journal_events


def _end_turn_for_block(
    *,
    entities: Dict[str, Any],
    turn_resources: Dict[str, Dict[str, Any]],
    active_block: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    updated_resources = copy.deepcopy(turn_resources or {})
    if not isinstance(updated_resources, dict):
        updated_resources = {}

    journal_events: List[str] = []
    for actor_id in active_block:
        actor = entities.get(actor_id, {})
        if isinstance(actor, dict):
            journal_events.extend(
                _apply_end_of_turn_status_effects(
                    entity_id=actor_id,
                    entity=actor,
                )
            )
        resources = updated_resources.get(actor_id)
        if not isinstance(resources, dict):
            resources = _default_turn_resources(actor_id, actor if isinstance(actor, dict) else {})
        resources["_turn_started"] = False
        updated_resources[actor_id] = resources
    return updated_resources, journal_events


def _build_turn_lock_result(
    *,
    state: Any,
    intent: str,
    actor_id: str,
    target_id: str,
    entities: Dict[str, Any],
) -> Dict[str, Any]:
    active_id = _get_active_turn_id(state)
    state_entities = state.get("entities") or {}
    active_entity = (
        state_entities.get(active_id, {}) if isinstance(state_entities, dict) else {}
    )
    actor_entity = entities.get(actor_id, {}) if isinstance(entities, dict) else {}
    active_name = _display_entity_name(active_entity, active_id or "unknown")
    actor_name = _display_entity_name(actor_entity, actor_id or "unknown")
    message = (
        f"[系统驳回] 动作无效！当前是 {active_name} 的回合，"
        f"你不能越权指挥 {actor_name} 行动。"
    )
    return {
        "journal_events": [message],
        "entities": entities,
        "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
        "combat_active": bool(state.get("combat_active", False)),
        "initiative_order": list(state.get("initiative_order") or []),
        "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
        "turn_resources": copy.deepcopy(state.get("turn_resources") or {}),
        "raw_roll_data": _build_action_result(
            intent=intent,
            actor=actor_id,
            target=target_id,
            is_success=False,
            result_type="TURN_LOCKED",
        ),
        "turn_locked": True,
    }


def _reject_if_not_active_turn(
    *,
    state: Any,
    intent: str,
    actor_id: str,
    target_id: str,
    entities: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not bool(state.get("combat_active", False)):
        return None
    initiative_order = list(state.get("initiative_order") or [])
    if not initiative_order:
        return None
    active_block = _get_active_turn_block(
        state=state,
        entities=entities,
        initiative_order=initiative_order,
    )
    if not active_block:
        return None
    if _normalize_entity_id(actor_id) in active_block:
        return None
    return _build_turn_lock_result(
        state=state,
        intent=intent,
        actor_id=_normalize_entity_id(actor_id),
        target_id=target_id,
        entities=entities,
    )


def _build_action_result(
    *,
    intent: str,
    actor: str,
    target: str,
    is_success: bool,
    result_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "intent": intent,
        "actor": actor,
        "target": target,
        "result": {
            "is_success": is_success,
            "result_type": result_type,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _resolve_loot_destination(
    *,
    actor_id: str,
    entities: Dict[str, Any],
    player_inventory: Dict[str, int],
) -> tuple[Dict[str, int], str]:
    if actor_id == "player":
        return player_inventory, "玩家"

    actor = entities.get(actor_id)
    if not isinstance(actor, dict):
        return player_inventory, "玩家"

    actor_inventory = actor.setdefault("inventory", {})
    if not isinstance(actor_inventory, dict):
        actor_inventory = {}
        actor["inventory"] = actor_inventory
    return actor_inventory, _display_entity_name(actor, actor_id)


def _is_hazard_lab_map(state: Any) -> bool:
    map_data = state.get("map_data") if isinstance(state, dict) else {}
    if not isinstance(map_data, dict):
        return False
    return str(map_data.get("id") or "").strip().lower() == "hazard_lab"


def _is_lab_corridor_door(target_id: str) -> bool:
    return _normalize_entity_id(target_id) == "door_b_to_d"


def _mark_act2_trap_revealed(flags: Dict[str, Any]) -> None:
    flags["act2_corridor_entered"] = True
    flags["act2_scout_perception_checked"] = True
    flags["act2_scout_perception_success"] = True
    flags["act2_gas_trap_revealed"] = True


def _mark_act2_disarm_attempt(
    flags: Dict[str, Any],
    *,
    actor_id: str,
    success: bool,
) -> None:
    normalized_actor = _normalize_entity_id(actor_id) or "player"
    if normalized_actor == "scout":
        flags["act2_scout_ordered_to_disarm"] = True
    flags["act2_disarm_actor"] = normalized_actor
    flags["act2_disarm_attempted"] = True
    flags["act2_disarm_success"] = bool(success)


def _mark_act2_trap_disarmed(flags: Dict[str, Any]) -> None:
    flags["act2_gas_trap_disarmed"] = True
    flags["act2_gas_trap_revealed"] = True


def _mark_act2_trap_triggered(flags: Dict[str, Any]) -> None:
    flags["act2_gas_trap_triggered"] = True
    flags["act2_gas_trap_damage_applied"] = True


def _has_inventory_item(
    *,
    actor_id: str,
    item_id: str,
    actor: Dict[str, Any],
    player_inventory: Dict[str, Any],
) -> bool:
    actor_inventory = actor.get("inventory") if isinstance(actor.get("inventory"), dict) else {}
    try:
        if int(actor_inventory.get(item_id, 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if _normalize_entity_id(actor_id) == "player":
        try:
            return int(player_inventory.get(item_id, 0) or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


def _sync_object_state(
    *,
    entities: Dict[str, Any],
    environment_objects: Dict[str, Any],
    target_id: str,
    updates: Dict[str, Any],
) -> None:
    for bucket in (entities, environment_objects):
        target = bucket.get(target_id)
        if isinstance(target, dict):
            target.update(updates)


def _mark_lab_corridor_door_base_flags(flags: Dict[str, Any]) -> None:
    flags["act2_corridor_exit_door_inspected"] = True
    flags["act2_corridor_exit_requires_key"] = True


def _mark_secret_study_hint(flags: Dict[str, Any]) -> None:
    flags["act2_secret_study_hint_given"] = True
    flags["act2_secret_study_route_unlocked"] = True


def _is_explicit_lab_lockpick_attempt(state: Any, intent_context: Dict[str, Any]) -> bool:
    action = str(intent_context.get("action") or "").strip().lower()
    source = str(intent_context.get("source") or "").strip().lower()
    user_input = str(state.get("user_input") or "") if isinstance(state, dict) else ""
    lowered = user_input.lower()
    negative_markers = (
        "不要撬锁",
        "别撬锁",
        "不撬锁",
        "不要开锁",
        "先别撬",
        "do not lockpick",
        "don't lockpick",
        "without lockpicking",
    )
    door_markers = (
        "door_b_to_d",
        "b-d",
        "bd门",
        "b_d",
        "实验室门",
        "实验室重门",
        "通往实验室",
        "重门",
        "lab door",
        "laboratory door",
    )
    if any(marker in user_input or marker in lowered for marker in negative_markers) and any(
        marker in user_input or marker in lowered for marker in door_markers
    ):
        return False
    if action == "lockpick_lab_door" or source in {"lockpick", "ui_lockpick", "lockpick_button"}:
        return True
    if not user_input:
        return True
    return any(marker in user_input or marker in lowered for marker in ("撬锁", "开锁", "撬开", "解锁", "lockpick", "pick the lock"))


def _flags_dict(state: Any) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    flags = state.get("flags")
    return dict(flags) if isinstance(flags, dict) else {}


def _is_gatekeeper_loot_source(*, target_id: str, target_obj: Dict[str, Any]) -> bool:
    normalized_target_id = _normalize_entity_id(target_id)
    if normalized_target_id == "gatekeeper":
        return True
    source_entity_id = _normalize_entity_id(target_obj.get("source_entity_id"))
    return source_entity_id == "gatekeeper"


def _allow_hazard_lab_gatekeeper_loot(state: Any, *, target_id: str, target_obj: Dict[str, Any]) -> bool:
    if not _is_hazard_lab_map(state):
        return False
    if not _is_gatekeeper_loot_source(target_id=target_id, target_obj=target_obj):
        return False
    flags = _flags_dict(state)
    return bool(flags.get("world_hazard_lab_gatekeeper_defeated", False))


def _should_eventize_hazard_lab_gatekeeper_key_loot(
    state: Any,
    *,
    actor_id: str,
    target_id: str,
    target_obj: Dict[str, Any],
    loot_items: Dict[str, int],
) -> bool:
    if actor_id != "player":
        return False
    if not _is_hazard_lab_map(state):
        return False
    if not _is_gatekeeper_loot_source(target_id=target_id, target_obj=target_obj):
        return False
    if int(loot_items.get("heavy_iron_key", 0) or 0) <= 0:
        return False
    flags = _flags_dict(state)
    if bool(flags.get("hazard_lab_gatekeeper_key_looted", False)):
        return False
    return True


def _format_loot_entries(items: Dict[str, int]) -> str:
    registry = get_registry()
    entries: List[str] = []
    for item_id, count in items.items():
        if not item_id:
            continue
        try:
            qty = int(count)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        entries.append(f"{registry.get_name(item_id)} x {qty}")
    return ", ".join(entries)


def _is_unlockable_skill_success(
    *,
    intent: str,
    target_obj: Optional[Dict[str, Any]],
    result: Dict[str, Any],
) -> bool:
    if not bool(result.get("is_success", False)):
        return False

    if not isinstance(target_obj, dict):
        return False

    status = str(target_obj.get("status", "")).strip().lower()
    is_locked = bool(target_obj.get("is_locked", False)) or status == "locked"
    if not is_locked:
        return False

    normalized_intent = str(intent or "").strip().upper()
    return normalized_intent in {"SLEIGHT_OF_HAND", "ACTION", "UNLOCK"}


def execute_combat_attack(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    map_data: Optional[Dict[str, Any]] = None,
    weapon_profile: Optional[Dict[str, Any]] = None,
    force_advantage: bool = False,
) -> Dict[str, Any]:
    """
    执行一次最小化 D20 攻击检定。
    规则骨架：1d20 + 4 vs AC；命中后造成 1d8 + 2 伤害，并直接修改 defender.hp/status。
    """
    attacker_id = _normalize_entity_id(attacker.get("id", "player")) or "player"
    defender_id = _normalize_entity_id(defender.get("id", ""))
    attacker_name = _display_entity_name(attacker, attacker_id)
    defender_name = _display_entity_name(defender, defender_id)
    defender_ac = int(defender.get("ac", 10))
    weapon_profile = dict(weapon_profile or _get_weapon_profile(attacker))
    weapon_name = str(weapon_profile.get("name") or "徒手打击")
    if str(weapon_profile.get("id") or "") == "unarmed":
        weapon_name = "徒手打击"
    weapon_type = str(weapon_profile.get("weapon_type") or "").strip().lower()
    if weapon_type not in {"melee", "ranged"}:
        weapon_type = "ranged" if _coerce_int(weapon_profile.get("range"), 1) > 1 else "melee"
    ability_name = "DEX" if weapon_type == "ranged" else "STR"
    ability_display = _ability_display_name(ability_name)
    ability_modifier = calculate_ability_modifier(_get_ability_score(attacker, ability_name, 10))
    attack_modifier = DEFAULT_ATTACK_BONUS + ability_modifier
    weapon_range = max(1, _coerce_int(weapon_profile.get("range"), 1))
    damage_dice = str(weapon_profile.get("damage_dice", "1d4"))
    damage_type = str(weapon_profile.get("damage_type") or "").strip().lower() or "physical"
    weapon_damage_bonus = _coerce_int(weapon_profile.get("damage_bonus"), 0)
    ability_damage_bonus = max(0, ability_modifier)
    damage_bonus = weapon_damage_bonus + ability_damage_bonus
    attacker_x = _coerce_int(attacker.get("x"), 4)
    attacker_y = _coerce_int(attacker.get("y"), 9)
    defender_x = _coerce_int(defender.get("x"), attacker_x)
    defender_y = _coerce_int(defender.get("y"), attacker_y)

    if weapon_range > 1 and not check_line_of_sight(
        (attacker_x, attacker_y),
        (defender_x, defender_y),
        map_data or {},
    ):
        attack_text = (
            f"❌ [战斗检定] {attacker_name} 使用 {weapon_name} 对 {defender_name} 发起攻击失败："
            "目标不在视线范围内（被障碍物遮挡）。"
        )
        return {
            "journal_events": [attack_text],
            "raw_roll_data": {
                "intent": "ATTACK",
                "actor": attacker_id,
                "target": defender_id,
                "weapon": str(weapon_profile.get("id") or "unarmed"),
                "weapon_name": weapon_name,
                "weapon_type": weapon_type,
                "range": weapon_range,
                "dc": defender_ac,
                "modifier": attack_modifier,
                "ability": ability_name,
                "ability_modifier": ability_modifier,
                "damage": {
                    "rolls": [],
                    "formula": damage_dice,
                    "damage_type": damage_type,
                    "modifier": damage_bonus,
                    "total": 0,
                },
                "result": {
                    "total": 0,
                    "raw_roll": 0,
                    "rolls": [],
                    "is_success": False,
                    "result_type": "NO_LOS",
                    "target_ac": defender_ac,
                },
            },
        }

    defender_prone = _has_status_effect(defender, "prone")
    has_melee_advantage = weapon_type == "melee" and defender_prone
    has_attack_advantage = has_melee_advantage or bool(force_advantage)
    attack_rolls = [random.randint(1, 20)]
    if has_attack_advantage:
        attack_rolls.append(random.randint(1, 20))
    attack_roll = max(attack_rolls) if has_attack_advantage else attack_rolls[0]
    attack_total = attack_roll + attack_modifier
    is_hit = attack_total >= defender_ac
    dice_roll_result = 0
    damage_total = 0

    if is_hit:
        dice_roll_result = parse_dice_string(damage_dice)
        damage_total = max(1, dice_roll_result + damage_bonus)
        current_hp = int(defender.get("hp", 0))
        max_hp = int(defender.get("max_hp", current_hp))
        new_hp = max(0, current_hp - damage_total)
        defender["hp"] = new_hp
        defender["max_hp"] = max_hp
        defender["status"] = "dead" if new_hp <= 0 else "alive"

    prefix = "🏹" if weapon_type == "ranged" else "🎲"
    attack_mode = "远程攻击" if weapon_type == "ranged" else "攻击"
    attack_text = (
        f"{prefix} [战斗检定] {attacker_name} 使用 {weapon_name} 对 {defender_name} 发起{attack_mode}。"
        f"命中检定: {attack_roll}(+{attack_modifier}) = {attack_total} vs AC {defender_ac}，"
    )
    if has_attack_advantage:
        advantage_reasons: List[str] = []
        if has_melee_advantage:
            advantage_reasons.append("目标倒地")
        if force_advantage:
            advantage_reasons.append("潜袭先手")
        reason_text = "、".join(advantage_reasons) if advantage_reasons else "战术优势"
        attack_text += (
            f"因为{reason_text}，攻击获得优势 ({attack_rolls[0]}, {attack_rolls[1]}) -> {attack_roll}。"
        )
    if is_hit:
        damage_breakdown = f"{damage_dice}[掷出 {dice_roll_result}]"
        if weapon_damage_bonus != 0:
            weapon_sign = "+" if weapon_damage_bonus > 0 else "-"
            damage_breakdown += f" {weapon_sign} 武器加成 {abs(weapon_damage_bonus)}"
        if ability_damage_bonus > 0:
            damage_breakdown += f" + {ability_display}加成 {ability_damage_bonus}"
        attack_text += (
            f"命中！造成 {damage_total} 点伤害 "
            f"(伤害骰: {damage_breakdown})。"
        )
    else:
        attack_text += "未命中！"
    attack_text += f" [加成来源: {ability_display} {ability_modifier:+d}]"

    journal_events = [attack_text]
    if is_hit and defender.get("status") == "dead":
        journal_events.append(f"☠️ [战斗结果] {defender_name} 倒下了。")
    recent_barks: List[Dict[str, Any]] = []
    highlight_events: List[Tuple[str, Dict[str, Any]]] = []
    if attack_roll == 20:
        highlight_events.append(
            (
                "CRITICAL_HIT",
                {
                    "attack_roll": attack_roll,
                    "attack_total": attack_total,
                    "weapon_name": weapon_name,
                    "damage_total": damage_total,
                },
            )
        )
    if attack_roll == 1:
        highlight_events.append(
            (
                "CRITICAL_MISS",
                {
                    "attack_roll": attack_roll,
                    "attack_total": attack_total,
                    "weapon_name": weapon_name,
                },
            )
        )
    if is_hit and str(defender.get("status", "")).lower() == "dead":
        highlight_events.append(
            (
                "KILL",
                {
                    "attack_roll": attack_roll,
                    "attack_total": attack_total,
                    "weapon_name": weapon_name,
                    "damage_total": damage_total,
                },
            )
        )
    for event_type, bark_context in highlight_events:
        bark_entry = _generate_bark_for_event(
            entity_id=attacker_id,
            entity_name=attacker_name,
            event_type=event_type,
            target_name=defender_name,
            context=bark_context,
        )
        if not bark_entry:
            continue
        recent_barks.append(bark_entry)
        journal_events.append(f"💬 [台词] {bark_entry['entity_name']}: \"{bark_entry['text']}\"")

    return {
        "journal_events": journal_events,
        "recent_barks": recent_barks,
        "raw_roll_data": {
            "intent": "ATTACK",
            "actor": attacker_id,
            "target": defender_id,
            "weapon": str(weapon_profile.get("id") or "unarmed"),
            "weapon_name": weapon_name,
            "weapon_type": weapon_type,
            "range": weapon_range,
            "dc": defender_ac,
            "modifier": attack_modifier,
            "ability": ability_name,
            "ability_modifier": ability_modifier,
            "damage": {
                "rolls": [dice_roll_result] if dice_roll_result else [],
                "formula": damage_dice,
                "damage_type": damage_type,
                "modifier": damage_bonus,
                "total": damage_total,
            },
            "result": {
                "total": attack_total,
                "raw_roll": attack_roll,
                "rolls": attack_rolls,
                "is_success": is_hit,
                "result_type": "HIT" if is_hit else "MISS",
                "target_ac": defender_ac,
                "advantage": has_attack_advantage,
            },
        },
    }


def _collect_alive_occupied_positions(
    entities: Dict[str, Any],
    *,
    ignore_ids: Optional[set[str]] = None,
) -> List[Tuple[int, int]]:
    occupied_positions: List[Tuple[int, int]] = []
    ignored = ignore_ids or set()
    for entity_id, entity in entities.items():
        normalized_id = _normalize_entity_id(entity_id)
        if normalized_id in ignored:
            continue
        if not isinstance(entity, dict) or not _is_alive_entity(entity):
            continue
        if _is_door_entity(normalized_id, entity):
            continue
        if _is_trap_entity(normalized_id, entity):
            continue
        occupied_positions.append(
            (
                _coerce_int(entity.get("x"), 4),
                _coerce_int(entity.get("y"), 8),
            )
        )
    return occupied_positions


def _find_nearest_player_side_target(
    *,
    source_id: str,
    source_x: int,
    source_y: int,
    entities: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, Any]], int]:
    target_id = ""
    target_obj: Optional[Dict[str, Any]] = None
    target_distance = 999
    for candidate_id, candidate in entities.items():
        normalized_candidate_id = _normalize_entity_id(candidate_id)
        if normalized_candidate_id == source_id:
            continue
        if not isinstance(candidate, dict) or not _is_alive_entity(candidate):
            continue
        if not _is_player_side_entity(normalized_candidate_id, candidate):
            continue
        distance = _chebyshev_distance(
            actor_x=source_x,
            actor_y=source_y,
            target_x=_coerce_int(candidate.get("x"), source_x),
            target_y=_coerce_int(candidate.get("y"), source_y),
        )
        if distance < target_distance:
            target_id = normalized_candidate_id
            target_obj = candidate
            target_distance = distance
    return target_id, target_obj, target_distance


def _move_enemy_toward_target_with_astar(
    *,
    enemy_id: str,
    enemy: Dict[str, Any],
    target_x: int,
    target_y: int,
    desired_range: int,
    movement_points: int,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
) -> Tuple[int, int, int, str]:
    if movement_points <= 0:
        return (
            _coerce_int(enemy.get("x"), 4),
            _coerce_int(enemy.get("y"), 3),
            0,
            "move_points_empty",
        )
    enemy_x = _coerce_int(enemy.get("x"), 4)
    enemy_y = _coerce_int(enemy.get("y"), 3)
    occupied_positions = _collect_alive_occupied_positions(
        entities,
        ignore_ids={enemy_id},
    )
    path = a_star_path(
        (enemy_x, enemy_y),
        (target_x, target_y),
        map_data if isinstance(map_data, dict) else {},
        occupied_positions,
    )
    if not path or len(path) <= 1:
        return enemy_x, enemy_y, 0, "path_blocked"

    max_steps = min(movement_points, len(path) - 1)
    steps_taken = 0
    for idx in range(1, max_steps + 1):
        px, py = path[idx]
        steps_taken = idx
        if _chebyshev_distance(
            actor_x=px,
            actor_y=py,
            target_x=target_x,
            target_y=target_y,
        ) <= max(1, int(desired_range or 1)):
            break
    if steps_taken <= 0:
        return enemy_x, enemy_y, 0, "no_progress"
    new_x, new_y = path[steps_taken]
    enemy["x"] = new_x
    enemy["y"] = new_y
    return new_x, new_y, steps_taken, "moved"


def _cover_score(map_data: Dict[str, Any], x: int, y: int) -> int:
    score = 0
    for obstacle in (map_data.get("obstacles") or []):
        if not isinstance(obstacle, dict) or not _obstacle_blocks_los(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            ox = _coerce_int(raw_coord[0], -9999)
            oy = _coerce_int(raw_coord[1], -9999)
            if max(abs(ox - x), abs(oy - y)) == 1:
                score += 1
    return score


def _find_ranged_reposition_tile(
    *,
    enemy_id: str,
    enemy: Dict[str, Any],
    target: Dict[str, Any],
    map_data: Dict[str, Any],
    entities: Dict[str, Any],
    movement_points: int,
    min_distance: int,
    max_distance: int,
    require_los: bool,
    prefer_cover: bool = False,
) -> Tuple[Optional[Tuple[int, int]], List[Tuple[int, int]]]:
    if movement_points <= 0:
        return None, []

    enemy_x = _coerce_int(enemy.get("x"), 4)
    enemy_y = _coerce_int(enemy.get("y"), 3)
    target_x = _coerce_int(target.get("x"), enemy_x)
    target_y = _coerce_int(target.get("y"), enemy_y)
    occupied_positions = _collect_alive_occupied_positions(entities, ignore_ids={enemy_id})
    occupied_set = set(occupied_positions)

    width = _coerce_int(map_data.get("width"), 0) if isinstance(map_data, dict) else 0
    height = _coerce_int(map_data.get("height"), 0) if isinstance(map_data, dict) else 0
    blocked_tiles = _collect_blocked_movement_tiles(map_data if isinstance(map_data, dict) else {})

    if width > 0 and height > 0:
        min_x, max_x = 0, width - 1
        min_y, max_y = 0, height - 1
    else:
        min_x, max_x = enemy_x - movement_points - 2, enemy_x + movement_points + 2
        min_y, max_y = enemy_y - movement_points - 2, enemy_y + movement_points + 2

    best_tile: Optional[Tuple[int, int]] = None
    best_path: List[Tuple[int, int]] = []
    best_score = -10**9

    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            candidate = (x, y)
            if candidate != (enemy_x, enemy_y):
                if candidate in occupied_set or candidate in blocked_tiles:
                    continue
            distance = max(abs(x - target_x), abs(y - target_y))
            if distance < min_distance or distance > max_distance:
                continue
            if require_los and not check_line_of_sight((x, y), (target_x, target_y), map_data):
                continue

            path = a_star_path(
                (enemy_x, enemy_y),
                candidate,
                map_data if isinstance(map_data, dict) else {},
                occupied_positions,
            )
            if not path:
                continue
            steps = len(path) - 1
            if steps > movement_points:
                continue

            score = distance * 5 - steps
            if prefer_cover:
                score += _cover_score(map_data, x, y) * 4
            if candidate == (enemy_x, enemy_y):
                score += 1
            if score > best_score:
                best_score = score
                best_tile = candidate
                best_path = path

    return best_tile, best_path


def _classify_enemy_behavior(enemy_id: str, enemy: Dict[str, Any]) -> str:
    explicit = str(enemy.get("enemy_type") or "").strip().lower()
    if explicit in {"archer", "ranged", "shaman", "melee"}:
        return explicit
    if "shaman" in enemy_id:
        return "shaman"
    if "archer" in enemy_id:
        return "archer"
    equipment = _get_equipment(enemy)
    has_ranged = bool(str(equipment.get("ranged") or "").strip())
    if has_ranged:
        return "archer"
    return "melee"


def _enemy_cast_healing_word(
    *,
    enemy_id: str,
    enemy: Dict[str, Any],
    ally_id: str,
    ally: Dict[str, Any],
    map_data: Dict[str, Any],
    resource_pool: Dict[str, Any],
    journal_events: List[str],
) -> bool:
    spell_data = get_spell_data("healing_word")
    heal_dice = str(spell_data.get("heal") or spell_data.get("healing_dice") or "1d4")
    heal_range = max(1, _coerce_int(spell_data.get("range"), 6))
    enemy_name = _display_entity_name(enemy, enemy_id)
    ally_name = _display_entity_name(ally, ally_id)

    enemy_x = _coerce_int(enemy.get("x"), 4)
    enemy_y = _coerce_int(enemy.get("y"), 3)
    ally_x = _coerce_int(ally.get("x"), enemy_x)
    ally_y = _coerce_int(ally.get("y"), enemy_y)
    if _chebyshev_distance(
        actor_x=enemy_x,
        actor_y=enemy_y,
        target_x=ally_x,
        target_y=ally_y,
    ) > heal_range:
        return False
    if not check_line_of_sight((enemy_x, enemy_y), (ally_x, ally_y), map_data):
        return False

    wis_mod = calculate_ability_modifier(_get_ability_score(enemy, "WIS", 10))
    heal_roll = parse_dice_string(heal_dice)
    heal_amount = max(1, heal_roll + wis_mod)
    ally_hp = _coerce_int(ally.get("hp"), 0)
    ally_max_hp = _coerce_int(ally.get("max_hp"), ally_hp)
    healed_hp = min(ally_max_hp, ally_hp + heal_amount)
    actual_heal = max(0, healed_hp - ally_hp)
    ally["hp"] = healed_hp
    ally["max_hp"] = ally_max_hp
    if healed_hp > 0:
        ally["status"] = "alive"

    slots = _normalize_spell_slots(resource_pool.get("spell_slots"))
    slots["level_1"] = max(0, int(slots.get("level_1", 0) or 0) - 1)
    resource_pool["spell_slots"] = slots
    resource_pool["action"] = max(0, int(resource_pool.get("action", 0) or 0) - 1)
    journal_events.append(
        f"🩹 [敌方AI] {enemy_name} 施放了 治愈真言，恢复了 {ally_name} {actual_heal} 点生命值 "
        f"(治疗骰: {heal_dice}[掷出 {heal_roll}] + 感知加成 {wis_mod})。"
    )
    return True


def _enemy_cast_sacred_flame(
    *,
    enemy_id: str,
    enemy: Dict[str, Any],
    target_id: str,
    target: Dict[str, Any],
    map_data: Dict[str, Any],
    resource_pool: Dict[str, Any],
    journal_events: List[str],
) -> Optional[Dict[str, Any]]:
    spell_data = get_spell_data("sacred_flame")
    spell_name = str(spell_data.get("name") or "圣火术")
    spell_range = max(1, _coerce_int(spell_data.get("range"), 6))
    damage_dice = str(spell_data.get("damage") or "1d8")
    save_ability = str(spell_data.get("saving_throw") or "DEX").upper()
    damage_type = _damage_type_display_name(spell_data.get("damage_type"))

    enemy_name = _display_entity_name(enemy, enemy_id)
    target_name = _display_entity_name(target, target_id)
    enemy_x = _coerce_int(enemy.get("x"), 4)
    enemy_y = _coerce_int(enemy.get("y"), 3)
    target_x = _coerce_int(target.get("x"), enemy_x)
    target_y = _coerce_int(target.get("y"), enemy_y)
    distance = _chebyshev_distance(
        actor_x=enemy_x,
        actor_y=enemy_y,
        target_x=target_x,
        target_y=target_y,
    )
    if distance > spell_range:
        return None
    if not check_line_of_sight((enemy_x, enemy_y), (target_x, target_y), map_data):
        return None

    damage_roll = parse_dice_string(damage_dice)
    save_roll = random.randint(1, 20)
    save_mod = calculate_ability_modifier(_get_ability_score(target, save_ability, 10))
    save_total = save_roll + save_mod
    save_success = save_total >= DEFAULT_SPELL_SAVE_DC
    applied_damage = damage_roll // 2 if save_success else damage_roll
    current_hp = _coerce_int(target.get("hp"), 0)
    max_hp = _coerce_int(target.get("max_hp"), current_hp)
    new_hp = max(0, current_hp - applied_damage)
    target["hp"] = new_hp
    target["max_hp"] = max_hp
    target["status"] = "dead" if new_hp <= 0 else "alive"
    resource_pool["action"] = max(0, int(resource_pool.get("action", 0) or 0) - 1)

    outcome_text = "成功" if save_success else "失败"
    journal_events.append(
        f"💥 [敌方AI] {enemy_name} 施放了 {spell_name}！{target_name} 进行了 {_ability_display_name(save_ability)}豁免 "
        f"(1d20{save_mod:+d}={save_total} vs DC {DEFAULT_SPELL_SAVE_DC})，{outcome_text}！"
        f"受到了 {applied_damage} 点{damage_type}伤害。"
    )
    if new_hp <= 0:
        journal_events.append(f"☠️ [战斗结果] {target_name} 倒下了。")
    return {
        "intent": "CAST_SPELL",
        "actor": enemy_id,
        "target": target_id,
        "spell_id": "sacred_flame",
        "result": {
            "is_success": True,
            "result_type": "SUCCESS",
            "save_total": save_total,
            "save_success": save_success,
            "damage": applied_damage,
        },
    }


def execute_enemy_turn(enemy_id: str, state: Any) -> Dict[str, Any]:
    """
    敌方 Utility AI：
    - melee: 贴近最近玩家侧单位并攻击
    - archer: 优先保持 4-15 格并确保 LoS 射击
    - shaman: 残血队友优先治疗，否则以圣火术输出
    """
    entities = copy.deepcopy(state.get("entities") or {})
    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    enemy_id = _normalize_entity_id(enemy_id)
    enemy = entities.get(enemy_id)
    if not isinstance(enemy, dict) or not _is_alive_entity(enemy):
        return {"entities": entities, "journal_events": []}

    enemy_name = _display_entity_name(enemy, enemy_id)
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    if enemy_id not in turn_resources or not isinstance(turn_resources.get(enemy_id), dict):
        turn_resources[enemy_id] = _default_turn_resources(enemy_id, enemy)
    turn_resources, start_tick_events = _begin_turn_for_block(
        entities=entities,
        turn_resources=turn_resources,
        active_block=[enemy_id],
        force_reset=False,
    )
    enemy = entities.get(enemy_id)
    if not isinstance(enemy, dict) or not _is_alive_entity(enemy):
        return {
            "entities": entities,
            "journal_events": list(start_tick_events),
            "turn_resources": turn_resources,
        }

    resource_pool = turn_resources.get(enemy_id, {}) if isinstance(turn_resources, dict) else {}
    resource_pool = dict(resource_pool) if isinstance(resource_pool, dict) else {}
    if "movement" not in resource_pool:
        resource_pool["movement"] = _movement_budget_from_speed(enemy)
    if (
        _has_status_effect(enemy, "surprised")
        and int(resource_pool.get("action", 0) or 0) <= 0
        and int(resource_pool.get("bonus_action", 0) or 0) <= 0
        and int(resource_pool.get("movement", 0) or 0) <= 0
    ):
        journal_events = list(start_tick_events) + [f"[敌方AI] {enemy_name} 受惊未定，本回合无法行动。"]
        turn_resources[enemy_id] = resource_pool
        turn_resources, end_tick_events = _end_turn_for_block(
            entities=entities,
            turn_resources=turn_resources,
            active_block=[enemy_id],
        )
        journal_events.extend(end_tick_events)
        return {
            "entities": entities,
            "journal_events": journal_events,
            "turn_resources": turn_resources,
            "map_data": map_data,
        }

    enemy_x = _coerce_int(enemy.get("x"), 4)
    enemy_y = _coerce_int(enemy.get("y"), 3)
    target_id, target_obj, target_distance = _find_nearest_player_side_target(
        source_id=enemy_id,
        source_x=enemy_x,
        source_y=enemy_y,
        entities=entities,
    )
    if not target_id or not isinstance(target_obj, dict):
        return {
            "entities": entities,
            "journal_events": list(start_tick_events) + [f"👹 [敌方回合] {enemy_name} 找不到可攻击目标。"],
            "turn_resources": turn_resources,
        }

    target_name = _display_entity_name(target_obj, target_id)
    journal_events = list(start_tick_events) + [f"[敌方AI] {enemy_name} 锁定了 {target_name}。"]
    behavior = _classify_enemy_behavior(enemy_id, enemy)
    raw_roll_data: Optional[Dict[str, Any]] = None
    recent_barks: List[Dict[str, Any]] = []

    if behavior == "shaman":
        slots = _normalize_spell_slots(resource_pool.get("spell_slots"))
        if not slots:
            slots = _default_spell_slots(enemy_id, enemy)
        if slots:
            resource_pool["spell_slots"] = slots

        heal_target_id = ""
        heal_target_obj: Optional[Dict[str, Any]] = None
        lowest_ratio = 1.1
        for ally_id_raw, ally_obj in entities.items():
            ally_id = _normalize_entity_id(ally_id_raw)
            if ally_id == enemy_id:
                continue
            if not isinstance(ally_obj, dict) or not _is_alive_entity(ally_obj):
                continue
            if not _is_hostile_entity(ally_obj):
                continue
            max_hp = max(1, _coerce_int(ally_obj.get("max_hp"), _coerce_int(ally_obj.get("hp"), 1)))
            hp = max(0, _coerce_int(ally_obj.get("hp"), max_hp))
            ratio = hp / max_hp
            if ratio < 0.5 and ratio < lowest_ratio:
                lowest_ratio = ratio
                heal_target_id = ally_id
                heal_target_obj = ally_obj

        if (
            heal_target_id
            and isinstance(heal_target_obj, dict)
            and int(resource_pool.get("action", 0) or 0) > 0
            and int(resource_pool.get("spell_slots", {}).get("level_1", 0) or 0) > 0
        ):
            heal_range = max(1, _coerce_int(get_spell_data("healing_word").get("range"), 6))
            heal_target_x = _coerce_int(heal_target_obj.get("x"), enemy_x)
            heal_target_y = _coerce_int(heal_target_obj.get("y"), enemy_y)
            current_distance = _chebyshev_distance(
                actor_x=enemy_x,
                actor_y=enemy_y,
                target_x=heal_target_x,
                target_y=heal_target_y,
            )
            if current_distance > heal_range:
                moved_x, moved_y, moved_steps, moved_reason = _move_enemy_toward_target_with_astar(
                    enemy_id=enemy_id,
                    enemy=enemy,
                    target_x=heal_target_x,
                    target_y=heal_target_y,
                    desired_range=heal_range,
                    movement_points=max(0, _coerce_int(resource_pool.get("movement"), 0)),
                    entities=entities,
                    map_data=map_data,
                )
                if moved_steps > 0:
                    resource_pool["movement"] = max(
                        0,
                        _coerce_int(resource_pool.get("movement"), 0) - moved_steps,
                    )
                    enemy["position"] = f"靠近 {_display_entity_name(heal_target_obj, heal_target_id)}"
                    enemy_x, enemy_y = moved_x, moved_y
                    journal_events.append(
                        f"[敌方AI] {enemy_name} 贴近队友，移动到了 ({moved_x}, {moved_y})。"
                    )
                elif moved_reason == "move_points_empty":
                    journal_events.append(f"[敌方AI] {enemy_name} 移动力不足，无法靠近治疗目标。")

            if _enemy_cast_healing_word(
                enemy_id=enemy_id,
                enemy=enemy,
                ally_id=heal_target_id,
                ally=heal_target_obj,
                map_data=map_data,
                resource_pool=resource_pool,
                journal_events=journal_events,
            ):
                raw_roll_data = _build_action_result(
                    intent="CAST_SPELL",
                    actor=enemy_id,
                    target=heal_target_id,
                    is_success=True,
                    result_type="HEALING_WORD_SUCCESS",
                )

        if raw_roll_data is None and int(resource_pool.get("action", 0) or 0) > 0:
            target_id, target_obj, target_distance = _find_nearest_player_side_target(
                source_id=enemy_id,
                source_x=_coerce_int(enemy.get("x"), enemy_x),
                source_y=_coerce_int(enemy.get("y"), enemy_y),
                entities=entities,
            )
            if target_id and isinstance(target_obj, dict):
                target_x = _coerce_int(target_obj.get("x"), enemy_x)
                target_y = _coerce_int(target_obj.get("y"), enemy_y)
                sacred_range = max(1, _coerce_int(get_spell_data("sacred_flame").get("range"), 6))
                need_reposition = (
                    target_distance > sacred_range
                    or target_distance <= 1
                    or not check_line_of_sight(
                        (_coerce_int(enemy.get("x"), enemy_x), _coerce_int(enemy.get("y"), enemy_y)),
                        (target_x, target_y),
                        map_data,
                    )
                )
                if need_reposition:
                    tile, path = _find_ranged_reposition_tile(
                        enemy_id=enemy_id,
                        enemy=enemy,
                        target=target_obj,
                        map_data=map_data,
                        entities=entities,
                        movement_points=max(0, _coerce_int(resource_pool.get("movement"), 0)),
                        min_distance=2,
                        max_distance=sacred_range,
                        require_los=True,
                        prefer_cover=True,
                    )
                    if tile and path and len(path) > 1:
                        steps = min(max(0, _coerce_int(resource_pool.get("movement"), 0)), len(path) - 1)
                        new_x, new_y = path[steps]
                        if (new_x, new_y) != (
                            _coerce_int(enemy.get("x"), enemy_x),
                            _coerce_int(enemy.get("y"), enemy_y),
                        ):
                            enemy["x"] = new_x
                            enemy["y"] = new_y
                            enemy["position"] = f"靠近 {target_name}"
                            resource_pool["movement"] = max(
                                0,
                                _coerce_int(resource_pool.get("movement"), 0) - steps,
                            )
                            journal_events.append(
                                f"[敌方AI] {enemy_name} 借助掩体调整站位，移动到了 ({new_x}, {new_y})。"
                            )

                raw_roll_data = _enemy_cast_sacred_flame(
                    enemy_id=enemy_id,
                    enemy=enemy,
                    target_id=target_id,
                    target=target_obj,
                    map_data=map_data,
                    resource_pool=resource_pool,
                    journal_events=journal_events,
                )
                if raw_roll_data is None:
                    journal_events.append(f"[敌方AI] {enemy_name} 暂时找不到可施法的视线角度。")

    elif behavior == "archer":
        weapon_profile = _get_weapon_profile(enemy, preferred_slot="ranged")
        weapon_range = max(1, _coerce_int(weapon_profile.get("range"), 15))
        target_x = _coerce_int(target_obj.get("x"), enemy_x)
        target_y = _coerce_int(target_obj.get("y"), enemy_y)
        current_distance = _chebyshev_distance(
            actor_x=enemy_x,
            actor_y=enemy_y,
            target_x=target_x,
            target_y=target_y,
        )
        has_los = check_line_of_sight((enemy_x, enemy_y), (target_x, target_y), map_data)
        ideal_position = current_distance > 3 and current_distance <= weapon_range and has_los
        if not ideal_position:
            tile, path = _find_ranged_reposition_tile(
                enemy_id=enemy_id,
                enemy=enemy,
                target=target_obj,
                map_data=map_data,
                entities=entities,
                movement_points=max(0, _coerce_int(resource_pool.get("movement"), 0)),
                min_distance=4,
                max_distance=weapon_range,
                require_los=True,
                prefer_cover=False,
            )
            if tile and path and len(path) > 1:
                steps = min(max(0, _coerce_int(resource_pool.get("movement"), 0)), len(path) - 1)
                new_x, new_y = path[steps]
                if (new_x, new_y) != (enemy_x, enemy_y):
                    enemy["x"] = new_x
                    enemy["y"] = new_y
                    enemy["position"] = f"靠近 {target_name}"
                    resource_pool["movement"] = max(
                        0,
                        _coerce_int(resource_pool.get("movement"), 0) - steps,
                    )
                    enemy_x, enemy_y = new_x, new_y
                    journal_events.append(
                        f"[敌方AI] {enemy_name} 找到了远程射击位，移动到了 ({new_x}, {new_y})。"
                    )
            elif current_distance <= 1:
                moved_x, moved_y, moved_steps, _ = _move_enemy_toward_target_with_astar(
                    enemy_id=enemy_id,
                    enemy=enemy,
                    target_x=target_x,
                    target_y=target_y,
                    desired_range=4,
                    movement_points=max(0, _coerce_int(resource_pool.get("movement"), 0)),
                    entities=entities,
                    map_data=map_data,
                )
                if moved_steps > 0:
                    enemy["position"] = f"靠近 {target_name}"
                    enemy_x, enemy_y = moved_x, moved_y
                    resource_pool["movement"] = max(
                        0,
                        _coerce_int(resource_pool.get("movement"), 0) - moved_steps,
                    )
                    journal_events.append(
                        f"[敌方AI] {enemy_name} 尝试拉开距离，移动到了 ({moved_x}, {moved_y})。"
                    )

        enemy_x = _coerce_int(enemy.get("x"), enemy_x)
        enemy_y = _coerce_int(enemy.get("y"), enemy_y)
        final_distance = _chebyshev_distance(
            actor_x=enemy_x,
            actor_y=enemy_y,
            target_x=target_x,
            target_y=target_y,
        )
        final_los = check_line_of_sight((enemy_x, enemy_y), (target_x, target_y), map_data)
        if final_distance <= 1 and str(_get_equipment(enemy).get("main_hand") or "").strip():
            weapon_profile = _get_weapon_profile(enemy, preferred_slot="main_hand")
        elif not final_los or final_distance > weapon_range:
            journal_events.append(f"[敌方AI] {enemy_name} 没有清晰射线，暂缓射击。")
            weapon_profile = {}

        if weapon_profile and int(resource_pool.get("action", 0) or 0) > 0:
            enemy["id"] = enemy_id
            target_obj["id"] = target_id
            attack_result = execute_combat_attack(
                attacker=enemy,
                defender=target_obj,
                map_data=map_data,
                weapon_profile=weapon_profile,
            )
            journal_events.extend(attack_result.get("journal_events", []))
            recent_barks.extend(list(attack_result.get("recent_barks") or []))
            raw_roll_data = attack_result.get("raw_roll_data")
            attack_result_type = (
                str(((raw_roll_data or {}).get("result") or {}).get("result_type") or "").upper()
            )
            if attack_result_type != "NO_LOS":
                resource_pool["action"] = max(0, int(resource_pool.get("action", 0) or 0) - 1)

    else:
        target_x = _coerce_int(target_obj.get("x"), enemy_x)
        target_y = _coerce_int(target_obj.get("y"), enemy_y)
        weapon_profile = _select_attack_weapon_profile(
            attacker=enemy,
            defender=target_obj,
            prefer_ranged=False,
        )
        weapon_range = max(1, _coerce_int(weapon_profile.get("range"), 1))
        if target_distance > weapon_range:
            moved_x, moved_y, moved_steps, moved_reason = _move_enemy_toward_target_with_astar(
                enemy_id=enemy_id,
                enemy=enemy,
                target_x=target_x,
                target_y=target_y,
                desired_range=weapon_range,
                movement_points=max(0, _coerce_int(resource_pool.get("movement"), 0)),
                entities=entities,
                map_data=map_data,
            )
            if moved_steps > 0:
                enemy["position"] = f"靠近 {target_name}"
                enemy_x, enemy_y = moved_x, moved_y
                resource_pool["movement"] = max(0, _coerce_int(resource_pool.get("movement"), 0) - moved_steps)
                journal_events.append(
                    f"[敌方AI] {enemy_name} 绕过了障碍，移动到了 ({moved_x}, {moved_y})。"
                )
            elif moved_reason == "move_points_empty":
                journal_events.append(f"[敌方AI] {enemy_name} 移动力不足，无法逼近目标。")
            else:
                journal_events.append(f"[敌方AI] {enemy_name} 被地形与站位卡住，无法找到可行路径。")

        final_distance = _chebyshev_distance(
            actor_x=_coerce_int(enemy.get("x"), enemy_x),
            actor_y=_coerce_int(enemy.get("y"), enemy_y),
            target_x=target_x,
            target_y=target_y,
        )
        if final_distance <= weapon_range and int(resource_pool.get("action", 0) or 0) > 0:
            enemy["id"] = enemy_id
            target_obj["id"] = target_id
            attack_result = execute_combat_attack(
                attacker=enemy,
                defender=target_obj,
                map_data=map_data,
                weapon_profile=weapon_profile,
            )
            journal_events.extend(attack_result.get("journal_events", []))
            recent_barks.extend(list(attack_result.get("recent_barks") or []))
            raw_roll_data = attack_result.get("raw_roll_data")
            attack_result_type = (
                str(((raw_roll_data or {}).get("result") or {}).get("result_type") or "").upper()
            )
            if attack_result_type != "NO_LOS":
                resource_pool["action"] = max(0, int(resource_pool.get("action", 0) or 0) - 1)
        elif final_distance <= weapon_range:
            journal_events.append(f"[敌方AI] {enemy_name} 动作点数不足，无法发动攻击。")
        else:
            journal_events.append(f"[敌方AI] {enemy_name} 距离过远，原地待命。")

    if isinstance(turn_resources, dict):
        turn_resources[enemy_id] = resource_pool
        turn_resources, end_tick_events = _end_turn_for_block(
            entities=entities,
            turn_resources=turn_resources,
            active_block=[enemy_id],
        )
        journal_events.extend(end_tick_events)
    journal_events.extend(_materialize_loot_drops(entities))

    payload: Dict[str, Any] = {
        "entities": entities,
        "journal_events": journal_events,
        "map_data": map_data,
        "recent_barks": recent_barks,
    }
    if raw_roll_data:
        payload["raw_roll_data"] = raw_roll_data
    if isinstance(turn_resources, dict):
        payload["turn_resources"] = turn_resources
    return payload


def advance_combat_after_action(state: Any, action_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    玩家/队友动作后推进回合：敌人自动行动，队友暂时待命，直到再次轮到 player。
    """
    if not isinstance(action_result, dict):
        return action_result
    if action_result.get("turn_locked"):
        return action_result

    combat_active = bool(action_result.get("combat_active", state.get("combat_active", False)))
    combat_phase = str(
        action_result.get(
            "combat_phase",
            state.get("combat_phase", "IN_COMBAT" if combat_active else "OUT_OF_COMBAT"),
        )
    )
    initiative_order = list(action_result.get("initiative_order") or state.get("initiative_order") or [])
    if not combat_active or not initiative_order:
        return action_result

    entities = copy.deepcopy(action_result.get("entities") or state.get("entities") or {})
    if not isinstance(entities, dict) or not entities:
        return action_result

    journal_events = list(action_result.get("journal_events") or [])
    recent_barks: List[Dict[str, Any]] = list(action_result.get("recent_barks") or [])
    journal_events.extend(_materialize_loot_drops(entities))
    turn_resources = copy.deepcopy(action_result.get("turn_resources") or state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    initiative_order = _prune_initiative_order(initiative_order, entities)
    end_event = _combat_end_event(entities)
    if end_event:
        return _build_out_of_combat_result(
            action_result=action_result,
            entities=entities,
            journal_events=journal_events,
            include_victory_banner=not _combat_has_live_hostiles(entities),
            recent_barks=recent_barks,
        )
    if not initiative_order:
        return {
            **action_result,
            "entities": entities,
            "journal_events": journal_events,
            "combat_phase": "OUT_OF_COMBAT",
            "combat_active": False,
            "initiative_order": [],
            "current_turn_index": 0,
            "turn_resources": {},
        }

    current_turn_index = _coerce_int(
        action_result.get("current_turn_index", state.get("current_turn_index", 0)),
        0,
    )
    if action_result.get("skip_advance"):
        current_turn_index %= len(initiative_order)
    else:
        current_turn_index %= len(initiative_order)

    max_iterations = max(1, len(initiative_order) * 2)
    for _ in range(max_iterations):
        initiative_order = _prune_initiative_order(initiative_order, entities)
        end_event = _combat_end_event(entities)
        if end_event:
            return _build_out_of_combat_result(
                action_result=action_result,
                entities=entities,
                journal_events=journal_events,
                include_victory_banner=not _combat_has_live_hostiles(entities),
                recent_barks=recent_barks,
            )
        if not initiative_order:
            break

        current_turn_index %= len(initiative_order)
        active_block = _get_active_turn_block(
            state=state,
            entities=entities,
            initiative_order=initiative_order,
            current_turn_index=current_turn_index,
        )
        if not active_block:
            current_turn_index = (current_turn_index + 1) % len(initiative_order)
            continue

        block_entities = [entities.get(actor_id, {}) for actor_id in active_block]
        block_side = _turn_side_key(active_block[0], block_entities[0]) if block_entities else "neutral"

        if block_side == "party":
            turn_resources, start_tick_events = _begin_turn_for_block(
                entities=entities,
                turn_resources=turn_resources,
                active_block=active_block,
                force_reset=False,
            )
            journal_events.extend(start_tick_events)
            all_spent = True
            for actor_id in active_block:
                resources = turn_resources.get(actor_id, {})
                if int(resources.get("action", 0) or 0) > 0 or int(resources.get("bonus_action", 0) or 0) > 0:
                    all_spent = False
                    break
            if all_spent:
                turn_resources, end_tick_events = _end_turn_for_block(
                    entities=entities,
                    turn_resources=turn_resources,
                    active_block=active_block,
                )
                journal_events.extend(end_tick_events)
                for actor_id in active_block:
                    resources = turn_resources.get(actor_id, {})
                    if isinstance(resources, dict):
                        resources["action"] = 0
                        resources["bonus_action"] = 0
                        resources["movement"] = 0
                        turn_resources[actor_id] = resources
                current_turn_index = (current_turn_index + len(active_block)) % len(initiative_order)
                action_result = {**action_result, "turn_resources": turn_resources}
                continue
            break

        if block_side == "hostile":
            turn_resources, start_tick_events = _begin_turn_for_block(
                entities=entities,
                turn_resources=turn_resources,
                active_block=active_block,
                force_reset=True,
            )
            journal_events.extend(start_tick_events)
            for enemy_id in active_block:
                enemy_result = execute_enemy_turn(
                    enemy_id,
                    {
                        **state,
                        **action_result,
                        "entities": entities,
                        "turn_resources": turn_resources,
                    },
                )
                entities = copy.deepcopy(enemy_result.get("entities") or entities)
                journal_events.extend(enemy_result.get("journal_events", []))
                recent_barks.extend(list(enemy_result.get("recent_barks") or []))
                journal_events.extend(_materialize_loot_drops(entities))
                if "turn_resources" in enemy_result:
                    turn_resources = copy.deepcopy(enemy_result.get("turn_resources") or turn_resources)
            current_turn_index = (current_turn_index + len(active_block)) % len(initiative_order)
            action_result = {**action_result, "turn_resources": turn_resources}
            continue

        current_turn_index = (current_turn_index + len(active_block)) % len(initiative_order)

    return {
        **action_result,
        "entities": entities,
        "journal_events": journal_events,
        "recent_barks": recent_barks,
        "combat_phase": combat_phase if initiative_order else "OUT_OF_COMBAT",
        "combat_active": True,
        "initiative_order": initiative_order,
        "current_turn_index": current_turn_index if initiative_order else 0,
        "turn_resources": turn_resources,
    }


def execute_end_turn_action(state: Any) -> Dict[str, Any]:
    """
    放弃行动：结束当前行动者的回合。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    intent_context = state.get("intent_context") or {}
    active_id = _get_active_turn_id(state)
    if not bool(state.get("combat_active", False)) or not active_id:
        return {
            "journal_events": ["[系统提示] 当前不在战斗中，无法结束回合。"],
            "entities": entities,
        }

    requested_actor = _normalize_entity_id(intent_context.get("action_actor", active_id) or active_id)
    action_target = str(intent_context.get("action_target") or "").strip().lower()
    if requested_actor and requested_actor != active_id and action_target not in {"party", "group", "all"}:
        return _build_turn_lock_result(
            state=state,
            intent="END_TURN",
            actor_id=requested_actor,
            target_id="",
            entities=entities,
        )

    active_block = _get_active_turn_block(state=state, entities=entities)
    active_entity = entities.get(active_id, {})
    active_name = _display_entity_name(active_entity, active_id)
    if action_target in {"party", "group", "all"} and active_block:
        turn_resources, end_tick_events = _end_turn_for_block(
            entities=entities,
            turn_resources=turn_resources,
            active_block=active_block,
        )
        for actor_id in active_block:
            resources = turn_resources.get(actor_id, {})
            if isinstance(resources, dict):
                resources["action"] = 0
                resources["bonus_action"] = 0
                resources["movement"] = 0
                resources["_turn_started"] = False
                turn_resources[actor_id] = resources
        current_turn_index = (state.get("current_turn_index", 0) or 0) + len(active_block)
        return {
            "journal_events": [f"⏭️ [回合] {active_name} 宣布我方结束回合。"] + end_tick_events,
            "entities": entities,
            "combat_phase": "IN_COMBAT",
            "combat_active": True,
            "initiative_order": list(state.get("initiative_order") or []),
            "current_turn_index": int(current_turn_index),
            "turn_resources": turn_resources,
            "skip_advance": True,
            "raw_roll_data": _build_action_result(
                intent="END_TURN",
                actor=active_id,
                target="party",
                is_success=True,
                result_type="PASS_GROUP",
            ),
        }

    if active_block and requested_actor in active_block:
        turn_resources, end_tick_events = _end_turn_for_block(
            entities=entities,
            turn_resources=turn_resources,
            active_block=[requested_actor],
        )
        resources = turn_resources.get(requested_actor, {})
        if isinstance(resources, dict):
            resources["action"] = 0
            resources["bonus_action"] = 0
            resources["movement"] = 0
            resources["_turn_started"] = False
            turn_resources[requested_actor] = resources
    else:
        end_tick_events = []
    return {
        "journal_events": [f"⏭️ [回合] {active_name} 选择了待命。"] + end_tick_events,
        "entities": entities,
        "combat_phase": "IN_COMBAT",
        "combat_active": True,
        "initiative_order": list(state.get("initiative_order") or []),
        "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
        "turn_resources": turn_resources,
        "raw_roll_data": _build_action_result(
            intent="END_TURN",
            actor=active_id,
            target="",
            is_success=True,
            result_type="PASS",
        ),
    }


def execute_attack_action(state: Any) -> Dict[str, Any]:
    """
    从 Graph state 中解析 ATTACK 行为，执行攻击结算并返回新的 entities / journal / latest_roll 数据。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    map_data = state.get("map_data") if isinstance(state.get("map_data"), dict) else {}
    intent_context = state.get("intent_context") or {}
    attacker_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="ATTACK",
        actor_id=attacker_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock
    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [战斗检定] 攻击失败：未指定目标。"],
            "entities": entities,
        }
    if target_id not in entities or not isinstance(entities.get(target_id), dict):
        return {
            "journal_events": [f"❌ [战斗检定] 攻击失败：找不到目标 {target_id}。"],
            "entities": entities,
        }

    defender = entities[target_id]
    if str(defender.get("status", "alive")).lower() == "dead":
        defender_name = _display_entity_name(defender, target_id)
        return {
            "journal_events": [f"⚔️ [战斗检定] {defender_name} 已经倒下，无需再次攻击。"],
            "entities": entities,
        }

    raw_attacker = _ensure_actor_entity(actor_id=attacker_id, entities=entities, state=state)
    if not isinstance(raw_attacker, dict):
        return {
            "journal_events": [f"❌ [战斗检定] 攻击失败：找不到攻击者 {attacker_id}。"],
            "entities": entities,
        }

    combat_events: List[str] = []
    ambush_events: List[str] = []
    combat_fields: Dict[str, Any] = {}
    ambush_advantage = False
    defender_entity_type = str(defender.get("entity_type", "")).strip().lower()
    is_environmental_target = defender_entity_type in {"door", "trap", "powder_barrel", "loot_drop"}
    target_triggers_combat = (
        not is_environmental_target
        and _is_alive_entity(defender)
        and not _is_player_side_entity(target_id, defender)
    )
    if target_triggers_combat and not bool(state.get("combat_active", False)):
        if not _is_hostile_entity(defender):
            defender["faction"] = "hostile"
        ambush_advantage, ambush_events = _apply_ambush_opening(
            entities=entities,
            attacker_id=attacker_id,
        )
        combat_fields, combat_events = _initialize_combat_fields(state=state, entities=entities)

    turn_resources = _ensure_turn_resources_for_block(
        state={**state, **combat_fields},
        entities=entities,
        active_block=_get_active_turn_block(
            state={**state, **combat_fields},
            entities=entities,
        ),
        force_reset=False,
    )
    if attacker_id not in turn_resources or not isinstance(turn_resources.get(attacker_id), dict):
        turn_resources[attacker_id] = _default_turn_resources(attacker_id, raw_attacker)
    actor_resources = turn_resources.get(attacker_id, {})
    if int(actor_resources.get("action", 0) or 0) <= 0:
        return {
            "journal_events": [f"[系统驳回] 动作资源不足！{_display_entity_name(raw_attacker, attacker_id)} 本回合没有可用动作。"],
            "entities": entities,
            "combat_phase": str(
                combat_fields.get("combat_phase", state.get("combat_phase", "IN_COMBAT"))
            ),
            "combat_active": bool(state.get("combat_active", False)) or combat_fields.get("combat_active", False),
            "initiative_order": list(combat_fields.get("initiative_order") or state.get("initiative_order") or []),
            "current_turn_index": _coerce_int(
                combat_fields.get("current_turn_index", state.get("current_turn_index", 0)), 0
            ),
            "turn_resources": turn_resources,
            "raw_roll_data": _build_action_result(
                intent="ATTACK",
                actor=attacker_id,
                target=target_id,
                is_success=False,
                result_type="NO_ACTION",
            ),
            "turn_locked": True,
        }

    prefer_ranged = _wants_ranged_attack(str(state.get("user_input", "") or ""), intent_context)
    weapon_profile = _select_attack_weapon_profile(
        attacker=raw_attacker,
        defender=defender,
        prefer_ranged=prefer_ranged,
    )
    weapon_type = str(weapon_profile.get("weapon_type") or "").strip().lower()
    weapon_move_template = (
        "🚶 [战术走位] {actor_name} 调整步伐，逼近了 {target_name}。"
        if str(weapon_profile.get("id") or "") == "unarmed"
        else (
            "🚶 [战术走位] {actor_name} 调整站位，{target_name} 进入了射程。"
            if weapon_type == "ranged"
            else (
            f"🚶 [战术走位] {{actor_name}} 拔出 {weapon_profile.get('name', '武器')}，"
            "{target_name} 进入了射程。"
            )
        )
    )
    approach_events = _auto_approach_actor_to_target(
        entities=entities,
        state=state,
        actor_id=attacker_id,
        target=target_obj or defender,
        target_name=target_name or _display_entity_name(defender, target_id),
        desired_range=int(weapon_profile.get("range", 1)),
        journal_template=weapon_move_template,
    )
    attacker = dict(raw_attacker)
    attacker["id"] = attacker_id
    defender["id"] = target_id

    attack_result = execute_combat_attack(
        attacker=attacker,
        defender=defender,
        map_data=map_data,
        weapon_profile=weapon_profile,
        force_advantage=ambush_advantage,
    )
    attack_result_type = (
        str(((attack_result.get("raw_roll_data") or {}).get("result") or {}).get("result_type") or "").upper()
    )
    actor_resources = dict(actor_resources) if isinstance(actor_resources, dict) else {}
    if attack_result_type != "NO_LOS":
        actor_resources["action"] = max(0, int(actor_resources.get("action", 0) or 0) - 1)
    turn_resources[attacker_id] = actor_resources

    post_reaction_logs: List[str] = []
    if str(((attack_result.get("raw_roll_data") or {}).get("result") or {}).get("result_type") or "").upper() == "HIT":
        damage_payload = (attack_result.get("raw_roll_data") or {}).get("damage") or {}
        post_reaction_logs = _process_post_damage_reaction(
            target_id=target_id,
            target=defender,
            entities=entities,
            map_data=map_data,
            damage_type=str(damage_payload.get("damage_type") or ""),
            damage_source="attack",
            exploded_coords=set(),
        )

    result = {
        **attack_result,
        "entities": entities,
        "turn_resources": turn_resources,
        "map_data": map_data,
        **combat_fields,
    }
    attack_events = result.get("journal_events", [])
    result["journal_events"] = (
        ambush_events + attack_events[:1] + approach_events + attack_events[1:] + post_reaction_logs + combat_events
    )
    return result


def execute_shove_action(state: Any) -> Dict[str, Any]:
    """
    d20 推击 (Shove)：
    - 距离需 <= 1
    - 消耗 1 点 bonus_action
    - 对抗检定：攻击方 1d20+STR vs 防守方 1d20+max(STR, DEX)
    - 成功时将目标推后 1 格，并处理边界/障碍/占位/篝火地形结算
    """
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    intent_context = state.get("intent_context") or {}

    attacker_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="SHOVE",
        actor_id=attacker_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    target_id, target_obj, _ = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [推击] 推击失败：未指定目标。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="SHOVE",
                actor=attacker_id,
                target="",
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }
    if target_id not in entities or not isinstance(entities.get(target_id), dict):
        return {
            "journal_events": [f"❌ [推击] 推击失败：找不到目标 {target_id}。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="SHOVE",
                actor=attacker_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    attacker = _ensure_actor_entity(actor_id=attacker_id, entities=entities, state=state)
    defender = entities[target_id]
    attacker_name = _display_entity_name(attacker, attacker_id)
    defender_name = _display_entity_name(defender, target_id)

    if not _is_alive_entity(defender):
        return {
            "journal_events": [f"⚔️ [推击] {defender_name} 已经倒下，无需推击。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="SHOVE",
                actor=attacker_id,
                target=target_id,
                is_success=False,
                result_type="TARGET_DOWN",
            ),
        }

    attacker_x = _coerce_int(attacker.get("x"), 4)
    attacker_y = _coerce_int(attacker.get("y"), 9)
    defender_x = _coerce_int(defender.get("x"), attacker_x)
    defender_y = _coerce_int(defender.get("y"), attacker_y)
    distance = _chebyshev_distance(
        actor_x=attacker_x,
        actor_y=attacker_y,
        target_x=defender_x,
        target_y=defender_y,
    )
    if distance > 1:
        return {
            "journal_events": [f"❌ [推击] 推击失败：{defender_name} 距离过远（需要相邻）。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="SHOVE",
                actor=attacker_id,
                target=target_id,
                is_success=False,
                result_type="OUT_OF_RANGE",
                extra={"distance": distance},
            ),
        }

    combat_events: List[str] = []
    combat_fields: Dict[str, Any] = {}
    target_is_hostile = _is_hostile_entity(defender)
    if target_is_hostile and not bool(state.get("combat_active", False)):
        initiative_order, initiative_rolls, initiative_log = _roll_initiative(entities)
        if initiative_order:
            current_turn_index = 0
            first_block = _get_active_turn_block(
                state=state,
                entities=entities,
                initiative_order=initiative_order,
                current_turn_index=current_turn_index,
            )
            auto_enemy_turn = False
            if first_block:
                first_entity = entities.get(first_block[0], {})
                if isinstance(first_entity, dict) and _turn_side_key(first_block[0], first_entity) == "hostile":
                    auto_enemy_turn = True
            combat_fields = {
                "combat_phase": "IN_COMBAT",
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "initiative_rolls": initiative_rolls,
                "auto_enemy_turn": auto_enemy_turn,
            }
            combat_events.append(initiative_log)

    turn_resources = _ensure_turn_resources_for_block(
        state={**state, **combat_fields},
        entities=entities,
        active_block=_get_active_turn_block(
            state={**state, **combat_fields},
            entities=entities,
        ),
        force_reset=False,
    )
    if attacker_id not in turn_resources or not isinstance(turn_resources.get(attacker_id), dict):
        turn_resources[attacker_id] = _default_turn_resources(attacker_id, attacker)
    actor_resources = dict(turn_resources.get(attacker_id, {}))
    if int(actor_resources.get("bonus_action", 0) or 0) <= 0:
        return {
            "journal_events": [f"[系统驳回] 附赠动作不足！{attacker_name} 本回合无法执行推击。"],
            "entities": entities,
            "combat_phase": str(
                combat_fields.get("combat_phase", state.get("combat_phase", "IN_COMBAT"))
            ),
            "combat_active": bool(state.get("combat_active", False)) or combat_fields.get("combat_active", False),
            "initiative_order": list(combat_fields.get("initiative_order") or state.get("initiative_order") or []),
            "current_turn_index": _coerce_int(
                combat_fields.get("current_turn_index", state.get("current_turn_index", 0)), 0
            ),
            "turn_resources": turn_resources,
            "raw_roll_data": _build_action_result(
                intent="SHOVE",
                actor=attacker_id,
                target=target_id,
                is_success=False,
                result_type="NO_BONUS_ACTION",
            ),
            "turn_locked": True,
        }

    # Bonus action is consumed once the shove attempt is committed.
    actor_resources["bonus_action"] = max(0, int(actor_resources.get("bonus_action", 0) or 0) - 1)
    turn_resources[attacker_id] = actor_resources

    attacker_mod = calculate_ability_modifier(_get_ability_score(attacker, "STR", 10))
    defender_str_mod = calculate_ability_modifier(_get_ability_score(defender, "STR", 10))
    defender_dex_mod = calculate_ability_modifier(_get_ability_score(defender, "DEX", 10))
    defender_mod = max(defender_str_mod, defender_dex_mod)

    attacker_roll = random.randint(1, 20)
    defender_roll = random.randint(1, 20)
    attacker_total = attacker_roll + attacker_mod
    defender_total = defender_roll + defender_mod
    is_success = attacker_total > defender_total

    contest_log = (
        f"🤼 [推击] {attacker_name} 对 {defender_name} 发起推击！"
        f"力量对抗: {attacker_roll}(+{attacker_mod})={attacker_total} vs "
        f"{defender_roll}(+{defender_mod})={defender_total}。"
    )
    journal_events: List[str] = [contest_log]

    raw_result_type = "SUCCESS" if is_success else "CONTEST_FAIL"
    shove_destination = {"x": defender_x, "y": defender_y}
    fire_damage = 0
    fire_roll = 0
    exploded_coords: set[Tuple[int, int]] = set()
    recent_barks: List[Dict[str, Any]] = []

    if not is_success:
        journal_events.append(f"🛡️ [推击] {defender_name} 稳住了身形，推击失败。")
    else:
        push_dx = _sign(defender_x - attacker_x)
        push_dy = _sign(defender_y - attacker_y)
        if push_dx == 0 and push_dy == 0:
            push_dy = -1
        destination_x = defender_x + push_dx
        destination_y = defender_y + push_dy
        shove_destination = {"x": destination_x, "y": destination_y}

        width = _coerce_int(map_data.get("width"), 0) if isinstance(map_data, dict) else 0
        height = _coerce_int(map_data.get("height"), 0) if isinstance(map_data, dict) else 0
        if width > 0 and height > 0 and (
            destination_x < 0
            or destination_x >= width
            or destination_y < 0
            or destination_y >= height
        ):
            raw_result_type = "PUSH_BLOCKED_BOUNDARY"
            journal_events.append("🧱 [推击] 目标后方是地图边界，推击被阻断。")
        else:
            occupied_by = ""
            for entity_id, entity in entities.items():
                normalized_entity_id = _normalize_entity_id(entity_id)
                if normalized_entity_id in {attacker_id, target_id}:
                    continue
                if not isinstance(entity, dict) or not _is_alive_entity(entity):
                    continue
                entity_x = _coerce_int(entity.get("x"), 4)
                entity_y = _coerce_int(entity.get("y"), 8)
                if entity_x == destination_x and entity_y == destination_y:
                    occupied_by = _display_entity_name(entity, normalized_entity_id)
                    break

            if occupied_by:
                raw_result_type = "PUSH_BLOCKED_OCCUPIED"
                journal_events.append(f"🧱 [推击] 目标后方被 {occupied_by} 占据，推击失败。")
            else:
                blocked_tiles = _collect_blocked_movement_tiles(map_data) if isinstance(map_data, dict) else set()
                is_campfire_tile = _is_campfire_tile(map_data, destination_x, destination_y)
                if (destination_x, destination_y) in blocked_tiles and not is_campfire_tile:
                    obstacle_name = _find_blocking_obstacle_name(map_data, destination_x, destination_y)
                    raw_result_type = "PUSH_BLOCKED_OBSTACLE"
                    journal_events.append(f"🧱 [推击] 目标后方被{obstacle_name}阻挡，推击失败。")
                else:
                    defender["x"] = destination_x
                    defender["y"] = destination_y
                    defender["position"] = f"被推向 {attacker_name} 的反方向"
                    journal_events.append(f"💨 [推击] {defender_name} 被推到了 ({destination_x}, {destination_y})。")

                    if is_campfire_tile:
                        fire_roll = parse_dice_string("1d4")
                        fire_damage = max(1, fire_roll)
                        current_hp = int(defender.get("hp", 0))
                        max_hp = int(defender.get("max_hp", current_hp))
                        new_hp = max(0, current_hp - fire_damage)
                        defender["hp"] = new_hp
                        defender["max_hp"] = max_hp
                        defender["status"] = "dead" if new_hp <= 0 else "alive"
                        raw_result_type = "PUSHED_INTO_CAMPFIRE"
                        journal_events.append(
                            f"🔥 [环境] {defender_name} 被推进篝火，受到 {fire_damage} 点火焰伤害 "
                            f"(1d4[掷出 {fire_roll}])！"
                        )
                        bark_entry = _generate_bark_for_event(
                            entity_id=attacker_id,
                            entity_name=attacker_name,
                            event_type="ENVIRONMENTAL_SHOVE",
                            target_name=defender_name,
                            context={
                                "contest": {
                                    "attacker_total": attacker_total,
                                    "defender_total": defender_total,
                                },
                                "fire_damage": fire_damage,
                                "destination": {"x": destination_x, "y": destination_y},
                            },
                        )
                        if bark_entry:
                            recent_barks.append(bark_entry)
                            journal_events.append(
                                f"💬 [台词] {bark_entry['entity_name']}: \"{bark_entry['text']}\""
                            )
                        if defender.get("status") == "dead":
                            journal_events.append(f"☠️ [战斗结果] {defender_name} 倒下了。")
                        journal_events.extend(
                            _process_post_damage_reaction(
                                target_id=target_id,
                                target=defender,
                                entities=entities,
                                map_data=map_data if isinstance(map_data, dict) else {},
                                damage_type="fire",
                                damage_source="campfire",
                                exploded_coords=exploded_coords,
                            )
                        )
                    else:
                        _add_or_refresh_status_effect(defender, "prone", 1)
                        journal_events.append(f"🌀 [状态] {defender_name} 被推倒在地，进入倒地状态（1回合）。")

    payload = {
        "journal_events": journal_events + combat_events,
        "recent_barks": recent_barks,
        "entities": entities,
        "map_data": map_data if isinstance(map_data, dict) else {},
        "turn_resources": turn_resources,
        **combat_fields,
        "raw_roll_data": _build_action_result(
            intent="SHOVE",
            actor=attacker_id,
            target=target_id,
            is_success=(raw_result_type in {"SUCCESS", "PUSHED_INTO_CAMPFIRE"}),
            result_type=raw_result_type,
            extra={
                "distance": distance,
                "contest": {
                    "attacker_roll": attacker_roll,
                    "attacker_modifier": attacker_mod,
                    "attacker_total": attacker_total,
                    "defender_roll": defender_roll,
                    "defender_modifier": defender_mod,
                    "defender_total": defender_total,
                },
                "destination": shove_destination,
                "fire_damage": fire_damage,
            },
        ),
    }
    return payload


def execute_cast_spell_action(state: Any) -> Dict[str, Any]:
    """
    执行施法动作：支持单体法术与以施法者为中心的 3x3 范围法术（切比雪夫距离 <= 1）。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    intent_context = state.get("intent_context") or {}

    caster_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    spell_profile = _get_spell_profile(intent_context)
    spell_id = str(spell_profile.get("id") or "")

    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="CAST_SPELL",
        actor_id=caster_id,
        target_id=target_query or spell_id,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    if not spell_profile:
        return {
            "journal_events": ["❌ [法术] 施法失败：未识别到有效法术。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="CAST_SPELL",
                actor=caster_id,
                target=target_query,
                is_success=False,
                result_type="INVALID_SPELL",
            ),
        }

    caster = _ensure_actor_entity(actor_id=caster_id, entities=entities, state=state)
    if not _is_alive_entity(caster):
        caster_name = _display_entity_name(caster, caster_id)
        return {
            "journal_events": [f"❌ [法术] {caster_name} 已无法行动，无法施法。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="CAST_SPELL",
                actor=caster_id,
                target=target_query,
                is_success=False,
                result_type="CASTER_DOWN",
            ),
        }

    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    target_type = str(spell_profile.get("target_type") or "single")
    spell_name = str(spell_profile.get("name") or spell_id)
    spell_range = max(1, _coerce_int(spell_profile.get("range"), 1))
    save_ability = str(spell_profile.get("save_ability") or "DEX").upper()
    damage_dice = str(spell_profile.get("damage_dice") or "1d4")
    damage_type_name = _damage_type_display_name(spell_profile.get("damage_type"))
    slot_level_cost = max(0, _coerce_int(spell_profile.get("slot_level_cost"), 0))

    target_is_hostile = False
    if target_id and target_id in entities and isinstance(target_obj, dict):
        target_is_hostile = _is_hostile_entity(target_obj)
    elif target_type == "aoe":
        target_is_hostile = any(
            isinstance(entity, dict) and _is_alive_entity(entity) and _is_hostile_entity(entity)
            for entity in entities.values()
        )

    combat_events: List[str] = []
    ambush_events: List[str] = []
    combat_fields: Dict[str, Any] = {}
    if target_is_hostile and not bool(state.get("combat_active", False)):
        _, ambush_events = _apply_ambush_opening(
            entities=entities,
            attacker_id=caster_id,
        )
        combat_fields, combat_events = _initialize_combat_fields(state=state, entities=entities)

    turn_resources = _ensure_turn_resources_for_block(
        state={**state, **combat_fields},
        entities=entities,
        active_block=_get_active_turn_block(
            state={**state, **combat_fields},
            entities=entities,
        ),
        force_reset=False,
    )
    if caster_id not in turn_resources or not isinstance(turn_resources.get(caster_id), dict):
        turn_resources[caster_id] = _default_turn_resources(caster_id, caster)
    actor_resources = dict(turn_resources.get(caster_id, {}))

    if int(actor_resources.get("action", 0) or 0) <= 0:
        return {
            "journal_events": [f"[系统驳回] 动作资源不足！{_display_entity_name(caster, caster_id)} 本回合没有可用动作。"],
            "entities": entities,
            "combat_phase": str(
                combat_fields.get("combat_phase", state.get("combat_phase", "IN_COMBAT"))
            ),
            "combat_active": bool(state.get("combat_active", False)) or combat_fields.get("combat_active", False),
            "initiative_order": list(combat_fields.get("initiative_order") or state.get("initiative_order") or []),
            "current_turn_index": _coerce_int(
                combat_fields.get("current_turn_index", state.get("current_turn_index", 0)), 0
            ),
            "turn_resources": turn_resources,
            "raw_roll_data": _build_action_result(
                intent="CAST_SPELL",
                actor=caster_id,
                target=target_id,
                is_success=False,
                result_type="NO_ACTION",
            ),
            "turn_locked": True,
        }

    spell_slots = _normalize_spell_slots(actor_resources.get("spell_slots"))
    if not spell_slots:
        spell_slots = _default_spell_slots(caster_id, caster)
    if slot_level_cost > 0 and int(spell_slots.get("level_1", 0) or 0) < slot_level_cost:
        return {
            "journal_events": [f"❌ [法术] {spell_name} 施放失败：{_display_entity_name(caster, caster_id)} 的 1 环法术位不足。"],
            "entities": entities,
            "combat_phase": str(
                combat_fields.get("combat_phase", state.get("combat_phase", "IN_COMBAT"))
            ),
            "combat_active": bool(state.get("combat_active", False)) or combat_fields.get("combat_active", False),
            "initiative_order": list(combat_fields.get("initiative_order") or state.get("initiative_order") or []),
            "current_turn_index": _coerce_int(
                combat_fields.get("current_turn_index", state.get("current_turn_index", 0)), 0
            ),
            "turn_resources": turn_resources,
            "raw_roll_data": _build_action_result(
                intent="CAST_SPELL",
                actor=caster_id,
                target=target_id,
                is_success=False,
                result_type="NO_SPELL_SLOT",
            ),
            "turn_locked": True,
        }

    approach_events: List[str] = []
    anchor_target_id = target_id
    anchor_target_obj = target_obj if isinstance(target_obj, dict) else None
    anchor_target_name = target_name
    if target_type == "single":
        if not anchor_target_id or anchor_target_id not in entities or not isinstance(anchor_target_obj, dict):
            return {
                "journal_events": [f"❌ [法术] 施法失败：找不到目标 {target_query or target_id}。"],
                "entities": entities,
                "raw_roll_data": _build_action_result(
                    intent="CAST_SPELL",
                    actor=caster_id,
                    target=target_id,
                    is_success=False,
                    result_type="NOT_FOUND",
                ),
            }
        if not _is_alive_entity(anchor_target_obj):
            return {
                "journal_events": [f"❌ [法术] {anchor_target_name} 已经倒下，无法作为施法目标。"],
                "entities": entities,
                "raw_roll_data": _build_action_result(
                    intent="CAST_SPELL",
                    actor=caster_id,
                    target=anchor_target_id,
                    is_success=False,
                    result_type="INVALID_TARGET",
                ),
            }
    else:
        if not isinstance(anchor_target_obj, dict):
            nearest_hostile: Optional[Tuple[str, Dict[str, Any], int]] = None
            caster_x = _coerce_int(caster.get("x"), 4)
            caster_y = _coerce_int(caster.get("y"), 9)
            for candidate_id, candidate in entities.items():
                normalized_candidate_id = _normalize_entity_id(candidate_id)
                if normalized_candidate_id == caster_id:
                    continue
                if not isinstance(candidate, dict) or not _is_alive_entity(candidate) or not _is_hostile_entity(candidate):
                    continue
                distance = _chebyshev_distance(
                    actor_x=caster_x,
                    actor_y=caster_y,
                    target_x=_coerce_int(candidate.get("x"), caster_x),
                    target_y=_coerce_int(candidate.get("y"), caster_y),
                )
                if nearest_hostile is None or distance < nearest_hostile[2]:
                    nearest_hostile = (normalized_candidate_id, candidate, distance)
            if nearest_hostile is not None:
                anchor_target_id, anchor_target_obj, _ = nearest_hostile
                anchor_target_name = _display_entity_name(anchor_target_obj, anchor_target_id)

    if isinstance(anchor_target_obj, dict):
        approach_events = _auto_approach_actor_to_target(
            entities=entities,
            state=state,
            actor_id=caster_id,
            target=anchor_target_obj,
            target_name=anchor_target_name,
            desired_range=spell_range,
            journal_template=f"✨ [战术走位] {{actor_name}} 调整站位，{anchor_target_name} 进入了 {spell_name} 的施法范围。",
        )

    caster = entities.get(caster_id, caster)
    caster_name = _display_entity_name(caster, caster_id)
    caster_x = _coerce_int(caster.get("x"), 4)
    caster_y = _coerce_int(caster.get("y"), 9)

    if target_type == "single" and isinstance(anchor_target_obj, dict):
        target_x = _coerce_int(anchor_target_obj.get("x"), caster_x)
        target_y = _coerce_int(anchor_target_obj.get("y"), caster_y)
        if not check_line_of_sight(
            (caster_x, caster_y),
            (target_x, target_y),
            map_data if isinstance(map_data, dict) else {},
        ):
            return {
                "journal_events": [
                    f"❌ [法术] 施放失败：目标不在视线范围内（被障碍物遮挡）。"
                ] + approach_events,
                "entities": entities,
                "turn_resources": turn_resources,
                **combat_fields,
                "raw_roll_data": _build_action_result(
                    intent="CAST_SPELL",
                    actor=caster_id,
                    target=anchor_target_id,
                    is_success=False,
                    result_type="NO_LOS",
                ),
                "turn_locked": True,
            }

    if target_type != "single":
        center_x = caster_x
        center_y = caster_y
        aoe_shape = str(spell_profile.get("aoe") or "").strip().lower()
        if "centered" not in aoe_shape and isinstance(anchor_target_obj, dict):
            center_x = _coerce_int(anchor_target_obj.get("x"), caster_x)
            center_y = _coerce_int(anchor_target_obj.get("y"), caster_y)
        if not check_line_of_sight(
            (caster_x, caster_y),
            (center_x, center_y),
            map_data if isinstance(map_data, dict) else {},
        ):
            return {
                "journal_events": [
                    f"❌ [法术] 施放失败：目标区域不在视线范围内（被障碍物遮挡）。"
                ] + approach_events,
                "entities": entities,
                "turn_resources": turn_resources,
                **combat_fields,
                "raw_roll_data": _build_action_result(
                    intent="CAST_SPELL",
                    actor=caster_id,
                    target=anchor_target_id,
                    is_success=False,
                    result_type="NO_LOS",
                ),
                "turn_locked": True,
            }

    affected_targets: List[Tuple[str, Dict[str, Any], str]] = []
    if target_type == "single":
        if isinstance(anchor_target_obj, dict) and _is_alive_entity(anchor_target_obj):
            target_x = _coerce_int(anchor_target_obj.get("x"), caster_x)
            target_y = _coerce_int(anchor_target_obj.get("y"), caster_y)
            if _chebyshev_distance(
                actor_x=caster_x,
                actor_y=caster_y,
                target_x=target_x,
                target_y=target_y,
            ) > spell_range:
                return {
                    "journal_events": [f"❌ [法术] 施法失败：{anchor_target_name} 超出 {spell_name} 的射程。"] + approach_events,
                    "entities": entities,
                    "turn_resources": turn_resources,
                    **combat_fields,
                    "raw_roll_data": _build_action_result(
                        intent="CAST_SPELL",
                        actor=caster_id,
                        target=anchor_target_id,
                        is_success=False,
                        result_type="OUT_OF_RANGE",
                    ),
                }
            affected_targets.append((anchor_target_id, anchor_target_obj, anchor_target_name))
    else:
        for candidate_id, candidate in entities.items():
            normalized_candidate_id = _normalize_entity_id(candidate_id)
            if normalized_candidate_id == caster_id:
                continue
            if not isinstance(candidate, dict) or not _is_alive_entity(candidate):
                continue
            distance = _chebyshev_distance(
                actor_x=caster_x,
                actor_y=caster_y,
                target_x=_coerce_int(candidate.get("x"), caster_x),
                target_y=_coerce_int(candidate.get("y"), caster_y),
            )
            if distance <= 1:
                affected_targets.append(
                    (normalized_candidate_id, candidate, _display_entity_name(candidate, normalized_candidate_id))
                )

    actor_resources["action"] = max(0, int(actor_resources.get("action", 0) or 0) - 1)
    if slot_level_cost > 0:
        spell_slots["level_1"] = max(0, int(spell_slots.get("level_1", 0) or 0) - slot_level_cost)
    if spell_slots:
        actor_resources["spell_slots"] = spell_slots
    turn_resources[caster_id] = actor_resources

    damage_roll = parse_dice_string(damage_dice)
    journal_events: List[str] = []
    exploded_coords: set[Tuple[int, int]] = set()
    cast_phrase = (
        f"{caster_name} 消耗{slot_level_cost}环法术位，施放了 {spell_name}"
        if slot_level_cost > 0
        else f"{caster_name} 施放了 {spell_name}"
    )
    if not affected_targets:
        journal_events.append(f"💥 [法术] {cast_phrase}，但范围内没有目标。")
    save_results: List[Dict[str, Any]] = []
    for victim_id, victim, victim_name in affected_targets:
        save_roll = random.randint(1, 20)
        save_mod = calculate_ability_modifier(_get_ability_score(victim, save_ability, 10))
        save_total = save_roll + save_mod
        save_success = save_total >= DEFAULT_SPELL_SAVE_DC
        applied_damage = damage_roll // 2 if save_success else damage_roll
        victim_hp = _coerce_int(victim.get("hp"), 0)
        victim_max_hp = _coerce_int(victim.get("max_hp"), victim_hp)
        victim_new_hp = max(0, victim_hp - applied_damage)
        victim["hp"] = victim_new_hp
        victim["max_hp"] = victim_max_hp
        victim["status"] = "dead" if victim_new_hp <= 0 else "alive"

        outcome_text = "成功" if save_success else "失败"
        journal_events.append(
            f"💥 [法术] {cast_phrase}！"
            f"{victim_name} 进行了 {_ability_display_name(save_ability)}豁免 "
            f"(1d20{save_mod:+d}={save_total} vs DC {DEFAULT_SPELL_SAVE_DC})，{outcome_text}！"
            f"受到了 {applied_damage} 点{damage_type_name}伤害。"
        )
        if victim_new_hp <= 0:
            journal_events.append(f"☠️ [战斗结果] {victim_name} 倒下了。")
        journal_events.extend(
            _process_post_damage_reaction(
                target_id=victim_id,
                target=victim,
                entities=entities,
                map_data=map_data if isinstance(map_data, dict) else {},
                damage_type=str(spell_profile.get("damage_type") or ""),
                damage_source=spell_id,
                exploded_coords=exploded_coords,
            )
        )
        save_results.append(
            {
                "target": victim_id,
                "ability": save_ability,
                "dc": DEFAULT_SPELL_SAVE_DC,
                "raw_roll": save_roll,
                "modifier": save_mod,
                "total": save_total,
                "is_success": save_success,
                "damage": applied_damage,
            }
        )

    result = {
        "journal_events": ambush_events + journal_events + approach_events + combat_events,
        "entities": entities,
        "map_data": map_data if isinstance(map_data, dict) else {},
        "turn_resources": turn_resources,
        **combat_fields,
        "raw_roll_data": {
            "intent": "CAST_SPELL",
            "actor": caster_id,
            "target": anchor_target_id,
            "spell_id": spell_id,
            "spell_name": spell_name,
            "save_dc": DEFAULT_SPELL_SAVE_DC,
            "save_ability": save_ability,
            "damage": {
                "formula": damage_dice,
                "total": damage_roll,
                "type": damage_type_name,
            },
            "result": {
                "is_success": bool(affected_targets),
                "result_type": "SUCCESS" if affected_targets else "NO_TARGETS",
                "targets": [target_id for target_id, _, _ in affected_targets],
                "save_results": save_results,
            },
            "spell_slots_after_cast": spell_slots,
        },
    }
    return result


def execute_loot_action(state: Any) -> Dict[str, Any]:
    """
    处理玩家/队友对死亡实体或已打开目标的搜刮。
    支持 entities 与 environment_objects 作为 loot source。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="LOOT",
        actor_id=actor_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock
    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [搜刮] 搜刮失败：未指定目标。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="LOOT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }

    if not isinstance(target_obj, dict):
        return {
            "journal_events": [f"❌ [搜刮] 搜刮失败：找不到目标 {target_id}。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="LOOT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    target_entity_type = str(target_obj.get("entity_type", "")).strip().lower()
    target_is_character_like = target_id in entities and target_entity_type not in {
        "loot_drop",
        "door",
        "trap",
        "powder_barrel",
    }
    if target_is_character_like:
        target_hp = _coerce_int(target_obj.get("hp"), 0)
        target_status = str(target_obj.get("status", "")).strip().lower()
        if (
            target_hp > 0
            and target_status not in {"dead", "open", "opened"}
            and _allow_hazard_lab_gatekeeper_loot(
                state=state,
                target_id=target_id,
                target_obj=target_obj,
            )
        ):
            target_obj["hp"] = 0
            target_obj["status"] = "dead"
            target_obj["is_alive"] = False
            entities[target_id] = target_obj
            target_hp = 0
            target_status = "dead"
        if target_hp > 0 and target_status not in {"dead", "open", "opened"}:
            actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
            actor_name = _display_entity_name(actor, actor_id)
            return {
                "journal_events": [
                    f"❌ [搜刮] {actor_name} 想搜刮 {target_name}，但他还没死，你不能直接抢，当前还无法被搜刮。"
                ],
                "entities": entities,
                "environment_objects": environment_objects,
                "player_inventory": player_inventory,
                "raw_roll_data": _build_action_result(
                    intent="LOOT",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="TARGET_ALIVE",
                ),
            }

    # 若目标是已死亡并已掉落为 loot_drop 的敌人，自动重定向到其地面战利品。
    if (
        target_id in entities
        and isinstance(target_obj, dict)
        and bool(target_obj.get("loot_generated", False))
        and str(target_obj.get("status", "")).strip().lower() == "dead"
    ):
        for candidate_id, candidate in entities.items():
            if not isinstance(candidate, dict):
                continue
            if not _is_loot_drop_entity(candidate_id, candidate):
                continue
            if _normalize_entity_id(candidate.get("source_entity_id")) == target_id:
                target_id = _normalize_entity_id(candidate_id)
                target_obj = candidate
                target_name = _display_entity_name(candidate, target_id)
                break

    target_status = str(target_obj.get("status", "")).strip().lower()
    target_is_loot_drop = target_id in entities and _is_loot_drop_entity(target_id, target_obj)
    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    target_x = _coerce_int(target_obj.get("x"), actor_x)
    target_y = _coerce_int(target_obj.get("y"), actor_y)
    distance = _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=target_x,
        target_y=target_y,
    )
    approach_events: List[str] = []
    if not target_is_loot_drop:
        approach_events = _auto_approach_actor_to_target(
            entities=entities,
            state=state,
            actor_id=actor_id,
            target=target_obj,
            target_name=target_name,
        )
    elif distance > 1:
        actor_name = _display_entity_name(actor, actor_id)
        return {
            "journal_events": [f"❌ [搜刮] {actor_name} 距离战利品过远，必须相邻才能搜刮。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="LOOT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="OUT_OF_RANGE",
                extra={"distance": distance},
            ),
        }
    if target_status not in LOOTABLE_STATUSES:
        return {
            "journal_events": [f"❌ [搜刮] {target_name} 还无法被搜刮。"] + approach_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="LOOT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_LOOTABLE",
                extra={"status": target_status},
            ),
        }

    source_inventory = target_obj.setdefault("inventory", {})
    if not isinstance(source_inventory, dict):
        source_inventory = {}
        target_obj["inventory"] = source_inventory

    loot_items: Dict[str, int] = {}
    for item_id, count in list(source_inventory.items()):
        if not item_id:
            continue
        try:
            qty = int(count)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        loot_items[item_id] = qty

    destination_inventory, actor_name = _resolve_loot_destination(
        actor_id=actor_id,
        entities=entities,
        player_inventory=player_inventory,
    )
    pending_events: List[Dict[str, Any]] = []
    direct_loot_items = dict(loot_items)
    eventized_loot_items: Dict[str, int] = {}
    if _should_eventize_hazard_lab_gatekeeper_key_loot(
        state=state,
        actor_id=actor_id,
        target_id=target_id,
        target_obj=target_obj,
        loot_items=loot_items,
    ):
        key_count = int(loot_items.get("heavy_iron_key") or 0)
        if key_count > 0:
            direct_loot_items.pop("heavy_iron_key", None)
            eventized_loot_items["heavy_iron_key"] = key_count
            source_entity_id = _normalize_entity_id(target_id)
            event_suffix = f"{int(state.get('turn_count') or 0)}_{actor_id}_{source_entity_id}"
            pending_events.extend(
                [
                    {
                        "event_id": f"evt_loot_transfer_{event_suffix}",
                        "event_type": "actor_item_transaction_requested",
                        "actor_id": actor_id,
                        "turn_index": int(state.get("turn_count") or 0),
                        "visibility": "party",
                        "payload": {
                            "social_action": {
                                "action_type": "item_transfer",
                                "actor_id": actor_id,
                                "target_actor_id": actor_id,
                                "item_id": "heavy_iron_key",
                                "quantity": key_count,
                                "reason": "act4_gatekeeper_loot",
                            },
                            "transaction": {
                                "transaction_type": "transfer",
                                "from_entity": source_entity_id,
                                "to_entity": actor_id,
                                "item": "heavy_iron_key",
                                "quantity": key_count,
                                "accepted": True,
                                "reason": "act4_gatekeeper_loot",
                            },
                        },
                    },
                    {
                        "event_id": f"evt_loot_flag_{event_suffix}",
                        "event_type": "world_flag_changed",
                        "actor_id": actor_id,
                        "turn_index": int(state.get("turn_count") or 0),
                        "visibility": "party",
                        "payload": {
                            "key": "hazard_lab_gatekeeper_key_looted",
                            "value": True,
                        },
                    },
                    {
                        "event_id": f"evt_loot_flag_defeated_{event_suffix}",
                        "event_type": "world_flag_changed",
                        "actor_id": actor_id,
                        "turn_index": int(state.get("turn_count") or 0),
                        "visibility": "party",
                        "payload": {
                            "key": "world_hazard_lab_gatekeeper_defeated",
                            "value": True,
                        },
                    },
                    {
                        "event_id": f"evt_loot_memory_{event_suffix}",
                        "event_type": "actor_memory_update_requested",
                        "actor_id": actor_id,
                        "turn_index": int(state.get("turn_count") or 0),
                        "visibility": "party",
                        "payload": {
                            "scope": "party_shared",
                            "memory_type": "quest_progress",
                            "text": "Gatekeeper 已倒下，我们拿到了 heavy_iron_key，出口就在前方。",
                        },
                    },
                ]
            )

    for item_id, qty in direct_loot_items.items():
        destination_inventory[item_id] = destination_inventory.get(item_id, 0) + qty
        remaining = int(source_inventory.get(item_id, 0) or 0) - int(qty)
        if remaining > 0:
            source_inventory[item_id] = remaining
        else:
            source_inventory.pop(item_id, None)
    target_obj["inventory"] = source_inventory
    if target_is_loot_drop:
        source_name = str(target_obj.get("source_name") or target_name)
        if not target_obj["inventory"]:
            entities.pop(target_id, None)
    else:
        source_name = target_name

    display_loot_items = {**direct_loot_items, **eventized_loot_items}
    if display_loot_items:
        items_text = _format_loot_entries(display_loot_items)
        journal_events = [f"💰 [搜刮] {actor_name} 从 {source_name} 上搜刮到了: {items_text}。"] + approach_events
    else:
        journal_events = [f"💰 [搜刮] {actor_name} 搜刮了 {source_name}，但没有找到任何有价值的物品。"] + approach_events

    out = {
        "journal_events": journal_events,
        "entities": entities,
        "environment_objects": environment_objects,
        "player_inventory": player_inventory,
        "raw_roll_data": _build_action_result(
            intent="LOOT",
            actor=actor_id,
            target=target_id,
            is_success=True,
            result_type="SUCCESS",
            extra={
                "loot_items": display_loot_items,
                "eventized_items": eventized_loot_items,
                "eventized": bool(eventized_loot_items),
            },
        ),
    }
    if pending_events:
        out["pending_events"] = pending_events
    return out


def execute_use_item(state: Any) -> Dict[str, Any]:
    """
    执行物品使用：扣除背包物品，并把效果写回实体状态。
    当前最小闭环聚焦治疗类消耗品。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    intent = str(state.get("intent", "USE_ITEM") or "USE_ITEM").strip().upper()
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    item_id = str(
        intent_context.get("item_id")
        or intent_context.get("target_item")
        or intent_context.get("action_target")
        or ""
    ).strip().lower()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent=intent,
        actor_id=actor_id,
        target_id=item_id,
        entities=entities,
    )
    if turn_lock:
        return turn_lock
    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_inventory, actor_name = _resolve_inventory_for_actor(
        actor_id=actor_id,
        entities=entities,
        player_inventory=player_inventory,
    )
    in_combat = _is_in_combat_state(state)
    combat_phase = "IN_COMBAT" if in_combat else "OUT_OF_COMBAT"
    initiative_order = list(state.get("initiative_order") or [])
    current_turn_index = _coerce_int(state.get("current_turn_index"), 0)
    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    actor_resources: Dict[str, Any] = {}
    if in_combat:
        active_block = _get_active_turn_block(state=state, entities=entities)
        if not active_block:
            active_block = [actor_id]
        turn_resources = _ensure_turn_resources_for_block(
            state=state,
            entities=entities,
            active_block=active_block,
            force_reset=False,
        )
        if actor_id not in turn_resources or not isinstance(turn_resources.get(actor_id), dict):
            turn_resources[actor_id] = _default_turn_resources(actor_id, actor)
        actor_resources = dict(turn_resources.get(actor_id, {}))
        if int(actor_resources.get("bonus_action", 0) or 0) <= 0:
            return {
                "journal_events": [f"[系统驳回] 附赠动作不足！{actor_name} 本回合无法使用消耗品。"],
                "entities": entities,
                "player_inventory": player_inventory,
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
                "raw_roll_data": _build_action_result(
                    intent=intent,
                    actor=actor_id,
                    target=item_id,
                    is_success=False,
                    result_type="NO_BONUS_ACTION",
                ),
                "turn_locked": True,
            }
    if not item_id:
        payload: Dict[str, Any] = {
            "journal_events": ["❌ [物品使用] 使用失败：未指定物品。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_ITEM",
            ),
        }
        if in_combat:
            payload.update(
                {
                    "combat_phase": combat_phase,
                    "combat_active": True,
                    "initiative_order": initiative_order,
                    "current_turn_index": current_turn_index,
                    "turn_resources": turn_resources,
                }
            )
        return payload

    item_data = get_registry().get_item_data(item_id)
    item_name = get_registry().get_name(item_id)
    if not _is_consumable_item(item_id, item_data):
        logger.warning("拦截了 LLM 试图消耗非消耗品的行为: %s", item_id)
        payload = {
            "journal_events": [f"❌ [物品使用] {item_name} 不是可消耗物品，不能被使用消耗。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target=item_id,
                is_success=False,
                result_type="NOT_CONSUMABLE",
            ),
        }
        if in_combat:
            payload.update(
                {
                    "combat_phase": combat_phase,
                    "combat_active": True,
                    "initiative_order": initiative_order,
                    "current_turn_index": current_turn_index,
                    "turn_resources": turn_resources,
                }
            )
        return payload

    if actor_inventory.get(item_id, 0) <= 0:
        payload = {
            "journal_events": [f"❌ [物品使用] {actor_name} 的背包里没有 {item_name}。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target=item_id,
                is_success=False,
                result_type="ITEM_NOT_FOUND",
            ),
        }
        if in_combat:
            payload.update(
                {
                    "combat_phase": combat_phase,
                    "combat_active": True,
                    "initiative_order": initiative_order,
                    "current_turn_index": current_turn_index,
                    "turn_resources": turn_resources,
                }
            )
        return payload

    effect = apply_item_effect(item_id, item_data)

    actor_inventory[item_id] = actor_inventory.get(item_id, 0) - 1
    if actor_inventory[item_id] <= 0:
        del actor_inventory[item_id]
    journal_events: List[str]

    if effect.get("success") and effect.get("type") == "heal":
        heal_value = int(effect.get("value", 0))
        current_hp = int(actor.get("hp", 0))
        max_hp = int(actor.get("max_hp", current_hp or 20))
        new_hp = min(max_hp, current_hp + heal_value)
        actual_heal = max(0, new_hp - current_hp)
        actor["hp"] = new_hp
        actor["max_hp"] = max_hp
        journal_events = [
            f"🧪 [消耗] {actor_name} 喝下了 {item_name}，恢复了 {actual_heal} 点生命值。"
        ]
    else:
        journal_events = [f"🧪 [消耗] {actor_name} 使用了 {item_name}。"]

    if in_combat:
        actor_resources["bonus_action"] = max(0, int(actor_resources.get("bonus_action", 0) or 0) - 1)
        turn_resources[actor_id] = actor_resources

    payload = {
        "journal_events": journal_events,
        "entities": entities,
        "player_inventory": player_inventory,
        "raw_roll_data": _build_action_result(
            intent=intent,
            actor=actor_id,
            target=item_id,
            is_success=bool(effect.get("success", True)),
            result_type=str(effect.get("type", "generic")).upper(),
            extra={"effect": effect},
        ),
    }
    if in_combat:
        payload.update(
            {
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
            }
        )
    return payload


def execute_equip_action(state: Any) -> Dict[str, Any]:
    """
    装备物品：从执行者背包/玩家全局背包移入实体 equipment 槽位。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    intent_context = state.get("intent_context") or {}
    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    item_id = _resolve_item_id_from_context(intent_context)
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="EQUIP",
        actor_id=actor_id,
        target_id=item_id,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_inventory, actor_name = _resolve_inventory_for_actor(
        actor_id=actor_id,
        entities=entities,
        player_inventory=player_inventory,
    )
    in_combat = _is_in_combat_state(state)
    combat_phase = "IN_COMBAT" if in_combat else "OUT_OF_COMBAT"
    initiative_order = list(state.get("initiative_order") or [])
    current_turn_index = _coerce_int(state.get("current_turn_index"), 0)
    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    actor_resources: Dict[str, Any] = {}
    if in_combat:
        active_block = _get_active_turn_block(state=state, entities=entities)
        if not active_block:
            active_block = [actor_id]
        turn_resources = _ensure_turn_resources_for_block(
            state=state,
            entities=entities,
            active_block=active_block,
            force_reset=False,
        )
        if actor_id not in turn_resources or not isinstance(turn_resources.get(actor_id), dict):
            turn_resources[actor_id] = _default_turn_resources(actor_id, actor)
        actor_resources = dict(turn_resources.get(actor_id, {}))
        if int(actor_resources.get("action", 0) or 0) <= 0:
            return {
                "journal_events": [f"[系统驳回] 动作资源不足！{actor_name} 本回合没有可用动作。"],
                "entities": entities,
                "player_inventory": player_inventory,
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
                "raw_roll_data": _build_action_result(
                    intent="EQUIP",
                    actor=actor_id,
                    target=item_id,
                    is_success=False,
                    result_type="NO_ACTION",
                ),
                "turn_locked": True,
            }

    if not item_id:
        return {
            "journal_events": ["❌ [装备] 装备失败：未指定物品。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="EQUIP",
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_ITEM",
            ),
        }

    item_data = get_registry().get(item_id)
    item_name = get_registry().get_name(item_id)
    slot = _equipment_slot_for_item(item_data)
    if not slot:
        return {
            "journal_events": [f"❌ [装备] {item_name} 不能被装备。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="EQUIP",
                actor=actor_id,
                target=item_id,
                is_success=False,
                result_type="NOT_EQUIPPABLE",
            ),
        }

    if int(actor_inventory.get(item_id, 0) or 0) <= 0:
        return {
            "journal_events": [f"❌ [装备] {actor_name} 的背包里没有 {item_name}。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="EQUIP",
                actor=actor_id,
                target=item_id,
                is_success=False,
                result_type="ITEM_NOT_FOUND",
            ),
        }

    equipment = _get_equipment(actor)
    previous_item = str(equipment.get(slot) or "").strip().lower()
    if previous_item:
        actor_inventory[previous_item] = int(actor_inventory.get(previous_item, 0) or 0) + 1

    actor_inventory[item_id] = int(actor_inventory.get(item_id, 0) or 0) - 1
    if actor_inventory[item_id] <= 0:
        del actor_inventory[item_id]
    equipment[slot] = item_id
    if in_combat:
        actor_resources["action"] = max(0, int(actor_resources.get("action", 0) or 0) - 1)
        turn_resources[actor_id] = actor_resources

    swap_text = f"，并卸下了 {get_registry().get_name(previous_item)}" if previous_item else ""
    payload = {
        "journal_events": [f"🎒 [物品] {actor_name} 装备了 {item_name}{swap_text}。"],
        "entities": entities,
        "player_inventory": player_inventory,
        "raw_roll_data": _build_action_result(
            intent="EQUIP",
            actor=actor_id,
            target=item_id,
            is_success=True,
            result_type="SUCCESS",
            extra={"slot": slot},
        ),
    }
    if in_combat:
        payload.update(
            {
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
            }
        )
    return payload


def execute_unequip_action(state: Any) -> Dict[str, Any]:
    """
    卸下装备：从 equipment 槽位移回执行者背包/玩家全局背包。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    intent_context = state.get("intent_context") or {}
    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    requested_item_id = _resolve_item_id_from_context(intent_context)
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="UNEQUIP",
        actor_id=actor_id,
        target_id=requested_item_id,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_inventory, actor_name = _resolve_inventory_for_actor(
        actor_id=actor_id,
        entities=entities,
        player_inventory=player_inventory,
    )
    equipment = _get_equipment(actor)
    in_combat = _is_in_combat_state(state)
    combat_phase = "IN_COMBAT" if in_combat else "OUT_OF_COMBAT"
    initiative_order = list(state.get("initiative_order") or [])
    current_turn_index = _coerce_int(state.get("current_turn_index"), 0)
    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    actor_resources: Dict[str, Any] = {}
    if in_combat:
        active_block = _get_active_turn_block(state=state, entities=entities)
        if not active_block:
            active_block = [actor_id]
        turn_resources = _ensure_turn_resources_for_block(
            state=state,
            entities=entities,
            active_block=active_block,
            force_reset=False,
        )
        if actor_id not in turn_resources or not isinstance(turn_resources.get(actor_id), dict):
            turn_resources[actor_id] = _default_turn_resources(actor_id, actor)
        actor_resources = dict(turn_resources.get(actor_id, {}))
        if int(actor_resources.get("action", 0) or 0) <= 0:
            return {
                "journal_events": [f"[系统驳回] 动作资源不足！{actor_name} 本回合没有可用动作。"],
                "entities": entities,
                "player_inventory": player_inventory,
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
                "raw_roll_data": _build_action_result(
                    intent="UNEQUIP",
                    actor=actor_id,
                    target=requested_item_id,
                    is_success=False,
                    result_type="NO_ACTION",
                ),
                "turn_locked": True,
            }

    slot = ""
    item_id = ""
    if requested_item_id:
        for candidate_slot in ("main_hand", "ranged", "armor"):
            if str(equipment.get(candidate_slot) or "").strip().lower() == requested_item_id:
                slot = candidate_slot
                item_id = requested_item_id
                break
    else:
        for candidate_slot in ("main_hand", "ranged", "armor"):
            candidate_item = str(equipment.get(candidate_slot) or "").strip().lower()
            if candidate_item:
                slot = candidate_slot
                item_id = candidate_item
                break

    if not slot or not item_id:
        return {
            "journal_events": [f"❌ [装备] {actor_name} 没有可卸下的装备。"],
            "entities": entities,
            "player_inventory": player_inventory,
            "raw_roll_data": _build_action_result(
                intent="UNEQUIP",
                actor=actor_id,
                target=requested_item_id,
                is_success=False,
                result_type="NOT_EQUIPPED",
            ),
        }

    equipment[slot] = None
    actor_inventory[item_id] = int(actor_inventory.get(item_id, 0) or 0) + 1
    if in_combat:
        actor_resources["action"] = max(0, int(actor_resources.get("action", 0) or 0) - 1)
        turn_resources[actor_id] = actor_resources
    item_name = get_registry().get_name(item_id)
    payload = {
        "journal_events": [f"🎒 [物品] {actor_name} 卸下了 {item_name}。"],
        "entities": entities,
        "player_inventory": player_inventory,
        "raw_roll_data": _build_action_result(
            intent="UNEQUIP",
            actor=actor_id,
            target=item_id,
            is_success=True,
            result_type="SUCCESS",
            extra={"slot": slot},
        ),
    }
    if in_combat:
        payload.update(
            {
                "combat_phase": combat_phase,
                "combat_active": True,
                "initiative_order": initiative_order,
                "current_turn_index": current_turn_index,
                "turn_resources": turn_resources,
            }
        )
    return payload


def execute_stealth_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    intent_context = state.get("intent_context") or {}
    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_name = _display_entity_name(actor, actor_id)

    if _is_in_combat_state(state):
        return {
            "journal_events": [f"❌ [潜行] {actor_name} 处于战斗中，无法进入潜行。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="STEALTH",
                actor=actor_id,
                target="",
                is_success=False,
                result_type="IN_COMBAT",
            ),
        }

    if _has_status_effect(actor, "hidden"):
        return {
            "journal_events": [f"🫥 [潜行] {actor_name} 已经处于潜行状态。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent="STEALTH",
                actor=actor_id,
                target="",
                is_success=True,
                result_type="ALREADY_HIDDEN",
            ),
        }

    _add_or_refresh_status_effect(actor, "hidden", 999)
    actor["position"] = f"{actor.get('position') or 'camp_center'} · 潜行中"
    return {
        "journal_events": [f"🫥 [潜行] {actor_name} 压低了身形，进入潜行状态。"],
        "entities": entities,
        "raw_roll_data": _build_action_result(
            intent="STEALTH",
            actor=actor_id,
            target="",
            is_success=True,
            result_type="HIDDEN",
        ),
    }


def execute_short_rest_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    if _is_in_combat_state(state):
        return _build_rest_reject_in_combat(
            state=state,
            entities=entities,
            intent="SHORT_REST",
        )

    detail_logs: List[str] = []
    for entity_id, entity in _iter_living_player_side_entities(entities):
        current_hp = max(0, _coerce_int(entity.get("hp"), 0))
        max_hp = max(1, _coerce_int(entity.get("max_hp"), current_hp or 1))
        recover_amount = max(0, max_hp // 2)
        new_hp = min(max_hp, current_hp + recover_amount)
        actual_heal = max(0, new_hp - current_hp)
        entity["hp"] = new_hp
        entity["max_hp"] = max_hp
        if actual_heal > 0:
            detail_logs.append(
                f"🩹 [短休] {_display_entity_name(entity, entity_id)} 恢复了 {actual_heal} 点生命值 ({new_hp}/{max_hp})。"
            )

    return {
        "journal_events": ["⛺ [短休] 队伍原地小憩了片刻，包扎伤口，恢复了部分生命值。"] + detail_logs,
        "entities": entities,
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "raw_roll_data": _build_action_result(
            intent="SHORT_REST",
            actor=_normalize_entity_id((state.get("intent_context") or {}).get("action_actor", "player")) or "player",
            target="",
            is_success=True,
            result_type="SUCCESS",
        ),
    }


def execute_long_rest_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    if _is_in_combat_state(state):
        return _build_rest_reject_in_combat(
            state=state,
            entities=entities,
            intent="LONG_REST",
        )

    turn_resources: Dict[str, Dict[str, Any]] = {}
    detail_logs: List[str] = []
    for entity_id, entity in _iter_living_player_side_entities(entities):
        max_hp = max(1, _coerce_int(entity.get("max_hp"), _coerce_int(entity.get("hp"), 1)))
        entity["hp"] = max_hp
        entity["max_hp"] = max_hp

        effects = _ensure_status_effects(entity)
        remaining_effects: List[Dict[str, Any]] = []
        cleared_effect_names: List[str] = []
        for effect in effects:
            effect_type = str(effect.get("type", "")).strip().lower()
            if not effect_type:
                continue
            is_permanent = bool(effect.get("permanent", False))
            if effect_type in REST_CLEARABLE_NEGATIVE_EFFECTS and not is_permanent:
                effect_name = str(
                    (STATUS_EFFECT_LIBRARY.get(effect_type) or {}).get("name")
                    or effect_type
                )
                cleared_effect_names.append(effect_name)
                continue
            remaining_effects.append(effect)
        entity["status_effects"] = remaining_effects

        max_spell_slots = _max_spell_slots_for_entity(entity_id, entity)
        if max_spell_slots:
            entity["spell_slots"] = copy.deepcopy(max_spell_slots)

        fresh_resources = _default_turn_resources(entity_id, entity)
        fresh_resources["_turn_started"] = False
        if max_spell_slots:
            fresh_resources["spell_slots"] = copy.deepcopy(max_spell_slots)
        turn_resources[entity_id] = fresh_resources

        if cleared_effect_names:
            detail_logs.append(
                f"🧹 [长休] {_display_entity_name(entity, entity_id)} 的异常状态已清理：{', '.join(cleared_effect_names)}。"
            )

    return {
        "journal_events": ["🏕️ [长休] 队伍建立营地并休息了一整晚。生命值、法术位与精力已彻底恢复！"] + detail_logs,
        "entities": entities,
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "turn_resources": turn_resources,
        "raw_roll_data": _build_action_result(
            intent="LONG_REST",
            actor=_normalize_entity_id((state.get("intent_context") or {}).get("action_actor", "player")) or "player",
            target="",
            is_success=True,
            result_type="SUCCESS",
        ),
    }


def execute_move_action(state: Any) -> Dict[str, Any]:
    """
    极简移动：玩家/角色朝目标方向最多移动 3 格，并停在目标邻近格。
    """
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    flags = dict(state.get("flags") or {})
    intent = str(state.get("intent", "MOVE") or "MOVE").strip().upper()
    intent_context = state.get("intent_context") or {}
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent=intent,
        actor_id=actor_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock
    target_id, target, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    is_coordinate_target = False
    if not isinstance(target, dict):
        coordinate_target = _parse_coordinate_target(target_query)
        if coordinate_target is not None:
            is_coordinate_target = True
            target = {"x": coordinate_target[0], "y": coordinate_target[1], "name": f"坐标({coordinate_target[0]},{coordinate_target[1]})"}
            target_id = f"{coordinate_target[0]},{coordinate_target[1]}"
            target_name = str(target.get("name"))
    if not target_id:
        return {
            "journal_events": ["❌ [空间移动] 移动失败：未指定目标。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    if not isinstance(target, dict):
        return {
            "journal_events": [f"❌ [空间移动] 移动失败：找不到目标 {target_id}。"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    target_x = _coerce_int(target.get("x"), actor_x)
    target_y = _coerce_int(target.get("y"), actor_y)
    is_transition_tile_target = _find_transition_zone_at(
        map_data=map_data if isinstance(map_data, dict) else {},
        x=target_x,
        y=target_y,
    ) is not None
    if is_coordinate_target or is_transition_tile_target:
        new_x, new_y = target_x, target_y
    else:
        new_x, new_y = _move_toward_target(
            actor_x=actor_x,
            actor_y=actor_y,
            target_x=target_x,
            target_y=target_y,
        )
    collision_error = _validate_move_destination(
        state=state,
        entities=entities,
        actor_id=actor_id,
        destination_x=new_x,
        destination_y=new_y,
        map_data_override=map_data if isinstance(map_data, dict) else {},
    )
    if collision_error:
        return {
            "journal_events": [f"❌ [空间移动] {collision_error}"],
            "entities": entities,
            "raw_roll_data": _build_action_result(
                intent=intent,
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="COLLISION_BLOCKED",
                extra={"destination": {"x": new_x, "y": new_y}, "reason": collision_error},
            ),
        }
    actor["x"] = new_x
    actor["y"] = new_y
    actor["position"] = f"靠近 {target_name}"
    actor_name = _display_entity_name(actor, actor_id)

    journal_events = [f"🚶 [空间移动] {actor_name}移动到了 {target_name} 附近。"]
    trap_events = _evaluate_traps_after_move(
        entities=entities,
        map_data=map_data if isinstance(map_data, dict) else {},
        actor_id=actor_id,
        flags=flags,
        environment_objects=environment_objects,
    )
    journal_events.extend(trap_events)
    actor_after_traps = entities.get(actor_id) if isinstance(entities.get(actor_id), dict) else actor
    actor_still_alive = _is_alive_entity(actor_after_traps if isinstance(actor_after_traps, dict) else {})
    if _is_player_side_entity(actor_id, actor) and actor_still_alive:
        transition_zone = _find_transition_zone_at(
            map_data=map_data if isinstance(map_data, dict) else {},
            x=new_x,
            y=new_y,
        )
        if isinstance(transition_zone, dict):
            transition_payload = _execute_map_transition(
                entities=entities,
                transition_zone=transition_zone,
            )
            if transition_payload:
                transition_payload["journal_events"] = (
                    journal_events + list(transition_payload.get("journal_events") or [])
                )
                transition_payload["raw_roll_data"] = _build_action_result(
                    intent=intent,
                    actor=actor_id,
                    target=target_id,
                    is_success=True,
                    result_type="MAP_TRANSITION",
                    extra={
                        "destination": {"x": new_x, "y": new_y},
                        "target_map": str(transition_zone.get("target_map") or ""),
                    },
                )
                return transition_payload

    vision_result: Dict[str, Any] = {}
    if actor_still_alive:
        vision_result = _evaluate_vision_alert_after_move(
            state=state,
            entities=entities,
            actor_id=actor_id,
            map_data=map_data,
        )
        journal_events.extend(list(vision_result.get("journal_events") or []))

    payload: Dict[str, Any] = {
        "journal_events": journal_events,
        "entities": entities,
        "environment_objects": environment_objects,
        "flags": flags,
        "map_data": map_data if isinstance(map_data, dict) else {},
        "raw_roll_data": _build_action_result(
            intent=intent,
            actor=actor_id,
            target=target_id,
            is_success=True,
            result_type="SUCCESS",
            extra={"destination": {"x": new_x, "y": new_y}},
        ),
    }
    for key in ("combat_phase", "combat_active", "initiative_order", "current_turn_index", "turn_resources"):
        if key in vision_result:
            payload[key] = vision_result[key]
    return payload


def execute_trigger_trap_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    flags = dict(state.get("flags") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    intent_context = state.get("intent_context") or {}
    context = detect_poison_trap_trigger_context(
        {
            **(state if isinstance(state, dict) else {}),
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
        },
        str(state.get("user_input") or ""),
        intent_context if isinstance(intent_context, dict) else {},
    )
    actor_id = _normalize_entity_id(
        (context or {}).get("trigger_actor_id")
        or (intent_context.get("action_actor") if isinstance(intent_context, dict) else "")
        or "player"
    ) or "player"
    target_id = _normalize_entity_id(
        (context or {}).get("trap_id")
        or (intent_context.get("action_target") if isinstance(intent_context, dict) else "")
        or "gas_trap_1"
    ) or "gas_trap_1"

    if not context:
        return {
            "journal_events": [],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="TRIGGER_TRAP",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NO_TRIGGER",
            ),
        }

    trap = entities.get(target_id)
    if not isinstance(trap, dict):
        trap_obj = environment_objects.get(target_id)
        if isinstance(trap_obj, dict):
            pos = trap_obj.get("position")
            x = trap_obj.get("x")
            y = trap_obj.get("y")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                x, y = pos[0], pos[1]
            trap = {
                "name": trap_obj.get("name") or "毒气陷阱",
                "entity_type": "trap",
                "status": trap_obj.get("status") or "armed",
                "is_hidden": bool(trap_obj.get("is_hidden", True)),
                "x": x,
                "y": y,
                "hp": 1,
                "max_hp": 1,
                "ac": 10,
                "detect_dc": trap_obj.get("detect_dc", 13),
                "disarm_dc": trap_obj.get("disarm_dc", 15),
                "save_dc": trap_obj.get("save_dc", 13),
                "damage": trap_obj.get("damage") or "2d6",
                "damage_type": trap_obj.get("damage_type") or "poison",
                "trigger_radius": trap_obj.get("trigger_radius", 0),
            }
            entities[target_id] = trap
    if not isinstance(trap, dict):
        return {
            "journal_events": [f"❌ [陷阱触发] 找不到目标 {target_id}。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="TRIGGER_TRAP",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    journal_events = _trigger_trap_entity(
        trap_id=target_id,
        trap=trap,
        entities=entities,
        map_data=map_data,
        trigger_actor_id=actor_id,
        flags=flags,
        environment_objects=environment_objects,
        affected_actor_ids=list(context.get("affected_actor_ids") or []),
    )
    return {
        "journal_events": journal_events,
        "entities": entities,
        "environment_objects": environment_objects,
        "flags": flags,
        "map_data": map_data,
        "raw_roll_data": _build_action_result(
            intent="TRIGGER_TRAP",
            actor=actor_id,
            target=target_id,
            is_success=True,
            result_type="SUCCESS",
            extra={"affected_actor_ids": list(context.get("affected_actor_ids") or [])},
        ),
    }


def execute_interact_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    secret_study_context = detect_secret_study_entry_context(
        state if isinstance(state, dict) else {},
        str(state.get("user_input") or "") if isinstance(state, dict) else "",
        intent_context if isinstance(intent_context, dict) else {},
    )
    if secret_study_context:
        flags = dict(state.get("flags") or {})
        flags["act3_secret_study_entered"] = True
        flags["act3_secret_study_discovered"] = True
        flags["act3_cracked_wall_found"] = True
        flags["room_c_secret_study_discovered"] = True
        flags["room_c_secret_study_entered"] = True

        for reveal_id in ("door_b_to_c", "cracked_wall"):
            _sync_object_state(
                entities=entities,
                environment_objects=environment_objects,
                target_id=reveal_id,
                updates={"status": "discovered", "is_hidden": False, "is_open": True},
            )
        if isinstance(entities.get("door_b_to_c"), dict):
            entities["door_b_to_c"]["status"] = "open"
            entities["door_b_to_c"]["is_open"] = True
            entities["door_b_to_c"]["is_locked"] = False
        _sync_door_state_to_map(map_data=map_data, entities=entities)

        return {
            "journal_events": [
                str(secret_study_context.get("journal_line") or "[秘密书房] cracked_wall -> room_c_secret_study"),
                str(secret_study_context.get("narration") or "墙后的冷风带着纸灰味……狭窄书房暴露出来。"),
            ],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "flags": flags,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=str(secret_study_context.get("target_id") or target_query or "cracked_wall"),
                is_success=True,
                result_type="SECRET_STUDY_DISCOVERED",
                extra={"to_room": "room_c_secret_study"},
            ),
        }
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="INTERACT",
        actor_id=actor_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [交互] 交互失败：未指定目标。"],
            "entities": entities,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }
    if target_id not in entities or not isinstance(entities.get(target_id), dict):
        return {
            "journal_events": [f"❌ [交互] 交互失败：找不到目标 {target_id}。"],
            "entities": entities,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    door = entities[target_id]
    if not _is_door_entity(target_id, door):
        return {
            "journal_events": [f"❌ [交互] {target_name} 不是可开关的门。"],
            "entities": entities,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="INVALID_OBJECT",
            ),
        }

    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    door_x = _coerce_int(door.get("x"), actor_x)
    door_y = _coerce_int(door.get("y"), actor_y)
    distance = _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=door_x,
        target_y=door_y,
    )
    if distance > 1:
        return {
            "journal_events": [f"❌ [交互] {target_name} 距离过远，需相邻才能交互。"],
            "entities": entities,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="OUT_OF_RANGE",
                extra={"distance": distance},
            ),
        }

    normalized_target_id = _normalize_entity_id(target_id)
    player_inventory = copy.deepcopy(state.get("player_inventory") or {})
    actor_inventory = actor.get("inventory") if isinstance(actor.get("inventory"), dict) else {}
    has_heavy_key = (
        int(actor_inventory.get("heavy_iron_key", 0) or 0) > 0
        or (actor_id == "player" and int(player_inventory.get("heavy_iron_key", 0) or 0) > 0)
    )
    if normalized_target_id == "door_b_to_d":
        flags = dict(state.get("flags") or {})
        _mark_lab_corridor_door_base_flags(flags)
        has_lab_key = _has_inventory_item(
            actor_id=actor_id,
            item_id="lab_key",
            actor=actor,
            player_inventory=player_inventory,
        )
        if not has_lab_key:
            _mark_secret_study_hint(flags)
            return {
                "journal_events": [
                    "🚪 [系统] 实验室重门需要 lab_key；也可以明确尝试 DC 15 撬锁。",
                    "🕯️ [线索] 门框附近有冷风，旁边墙面传来空响；附近可能还有通往书房的入口。",
                ],
                "entities": entities,
                "environment_objects": environment_objects,
                "map_data": map_data,
                "flags": flags,
                "demo_cleared": bool(state.get("demo_cleared", False)),
                "raw_roll_data": _build_action_result(
                    intent="INTERACT",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="MISSING_KEY",
                    extra={"key_required": "lab_key", "lockpick_dc": _coerce_int(door.get("lockpick_dc"), 15)},
                ),
            }

        door["is_locked"] = False
        door["is_open"] = True
        door["status"] = "open"
        _sync_object_state(
            entities=entities,
            environment_objects=environment_objects,
            target_id=target_id,
            updates={"is_locked": False, "is_open": True, "status": "open"},
        )
        _sync_door_state_to_map(map_data=map_data, entities=entities)
        flags["act2_corridor_exit_opened_with_key"] = True
        flags["act2_lockpick_success_route_to_boss"] = False
        return {
            "journal_events": ["🚪 [交互] lab_key 嵌入锁孔，通往实验室的重门打开了。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "flags": flags,
            "demo_cleared": bool(state.get("demo_cleared", False)),
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="SUCCESS",
                extra={"is_open": True, "key_required": "lab_key"},
            ),
        }
    if normalized_target_id == "heavy_oak_door_1" and not has_heavy_key:
        payload: Dict[str, Any] = {
            "journal_events": ["🚪 [系统] 门被锁死了，你需要一把沉重的铁钥匙。"],
            "entities": entities,
            "map_data": map_data,
            "demo_cleared": bool(state.get("demo_cleared", False)),
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="MISSING_KEY",
            ),
        }
        if bool(state.get("combat_active", False)):
            payload.update(
                {
                    "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
                    "combat_active": True,
                    "initiative_order": list(state.get("initiative_order") or []),
                    "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
                    "turn_resources": copy.deepcopy(state.get("turn_resources") or {}),
                }
            )
        return payload

    turn_resources = copy.deepcopy(state.get("turn_resources") or {})
    if not isinstance(turn_resources, dict):
        turn_resources = {}
    if bool(state.get("combat_active", False)):
        if actor_id not in turn_resources or not isinstance(turn_resources.get(actor_id), dict):
            turn_resources[actor_id] = _default_turn_resources(actor_id, actor)
        actor_resources = dict(turn_resources.get(actor_id, {}))
        if int(actor_resources.get("bonus_action", 0) or 0) <= 0:
            return {
                "journal_events": [f"[系统驳回] 附赠动作不足！{_display_entity_name(actor, actor_id)} 无法进行门交互。"],
                "entities": entities,
                "map_data": map_data,
                "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
                "combat_active": bool(state.get("combat_active", False)),
                "initiative_order": list(state.get("initiative_order") or []),
                "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
                "turn_resources": turn_resources,
                "raw_roll_data": _build_action_result(
                    intent="INTERACT",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="NO_BONUS_ACTION",
                ),
                "turn_locked": True,
            }
        actor_resources["bonus_action"] = max(0, int(actor_resources.get("bonus_action", 0) or 0) - 1)
        turn_resources[actor_id] = actor_resources

    if normalized_target_id == "heavy_oak_door_1":
        door["is_open"] = True
        door["status"] = "open"
        _sync_object_state(
            entities=entities,
            environment_objects=environment_objects,
            target_id=target_id,
            updates={"is_locked": False, "is_open": True, "status": "open"},
        )
        _sync_door_state_to_map(map_data=map_data, entities=entities)
        flags = dict(state.get("flags") or {})
        flags["hazard_lab_escape_complete"] = True
        flags["content_sprint_1_complete"] = True
        flags["act4_final_exit_opened"] = True
        closing_line = ""
        if bool(flags.get("act4_lab_poison_leak", False)):
            closing_line = 'Scout “下次我们可以试试不把实验室弄成毒锅。”'
        elif bool(flags.get("act4_negotiation_success", False)):
            closing_line = 'Analyst “有些牢笼不是用铁做的……”'
        elif bool(flags.get("act4_scout_steal_key_success", False)):
            closing_line = 'Scout “不流血，不讲道德，只是专业。”'
        elif bool(flags.get("act4_assault_success", False)):
            closing_line = 'Tactician “门开了。迟来的效率，仍然是效率。”'
        journal_events = [
            "🚪 [系统] 伴随着沉重的摩擦声，大门被推开了！一缕阳光照进地下室... **[DEMO CLEARED]**"
        ]
        if closing_line:
            journal_events.append(closing_line)
        payload = {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "flags": flags,
            "demo_cleared": True,
            "raw_roll_data": _build_action_result(
                intent="INTERACT",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="SUCCESS",
                extra={"is_open": True, "demo_cleared": True},
            ),
        }
        if bool(state.get("combat_active", False)):
            payload.update(
                {
                    "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
                    "combat_active": True,
                    "initiative_order": list(state.get("initiative_order") or []),
                    "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
                    "turn_resources": turn_resources,
                }
            )
        return payload

    new_is_open = not bool(door.get("is_open", False))
    door["is_open"] = new_is_open
    door["status"] = "open" if new_is_open else "closed"
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    actor_name = _display_entity_name(actor, actor_id)
    door_name = _display_entity_name(door, target_id)
    action_text = "打开了" if new_is_open else "关上了"

    payload: Dict[str, Any] = {
        "journal_events": [f"🚪 [交互] {actor_name} {action_text} {door_name}。"],
        "entities": entities,
        "map_data": map_data,
        "demo_cleared": bool(state.get("demo_cleared", False)),
        "raw_roll_data": _build_action_result(
            intent="INTERACT",
            actor=actor_id,
            target=target_id,
            is_success=True,
            result_type="SUCCESS",
            extra={"is_open": new_is_open},
        ),
    }
    if bool(state.get("combat_active", False)):
        payload.update(
            {
                "combat_phase": str(state.get("combat_phase", "IN_COMBAT")),
                "combat_active": True,
                "initiative_order": list(state.get("initiative_order") or []),
                "current_turn_index": _coerce_int(state.get("current_turn_index"), 0),
                "turn_resources": turn_resources,
            }
        )
    return payload


def execute_disarm_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    flags = dict(state.get("flags") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    _sync_door_state_to_map(map_data=map_data, entities=entities)
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="DISARM",
        actor_id=actor_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [解除陷阱] 解除失败：未指定目标。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }
    if target_id not in entities or not isinstance(entities.get(target_id), dict):
        return {
            "journal_events": [f"❌ [解除陷阱] 解除失败：找不到目标 {target_id}。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    trap = entities[target_id]
    if not _is_trap_entity(target_id, trap):
        return {
            "journal_events": [f"❌ [解除陷阱] {target_name} 不是可解除的陷阱。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="INVALID_OBJECT",
            ),
        }

    is_lab_scout_trap = (
        _is_hazard_lab_map(state)
        and _normalize_entity_id(target_id) == "gas_trap_1"
        and actor_id == "scout"
    )
    scout_saw_trap = bool(flags.get("hazard_lab_poison_trap_revealed", False)) or bool(
        flags.get("scout_detected_gas_trap", False)
        if not isinstance(flags.get("scout_detected_gas_trap"), dict)
        else flags.get("scout_detected_gas_trap", {}).get("value", False)
    )
    if bool(trap.get("is_hidden", True)) and not (is_lab_scout_trap and scout_saw_trap):
        return {
            "journal_events": [f"❌ [解除陷阱] {target_name} 仍处于隐藏状态，无法解除。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="HIDDEN_TRAP",
            ),
        }

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_name = _display_entity_name(actor, actor_id)
    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    trap_x = _coerce_int(trap.get("x"), actor_x)
    trap_y = _coerce_int(trap.get("y"), actor_y)
    distance = _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=trap_x,
        target_y=trap_y,
    )
    if distance > 1:
        if is_lab_scout_trap and scout_saw_trap:
            actor["x"] = trap_x
            actor["y"] = trap_y + 1
            actor["position"] = f"靠近 {target_name}"
        else:
            return {
                "journal_events": [f"❌ [解除陷阱] {target_name} 距离过远，需相邻才能解除。"],
                "entities": entities,
                "environment_objects": environment_objects,
                "flags": flags,
                "map_data": map_data,
                "raw_roll_data": _build_action_result(
                    intent="DISARM",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="OUT_OF_RANGE",
                    extra={"distance": distance},
                ),
            }

    if is_lab_scout_trap and scout_saw_trap:
        trap_name = _display_entity_name(trap, target_id)
        force_failure = bool(intent_context.get("force_disarm_failure", False)) or bool(
            flags.get("qa_force_trap_disarm_failure", False)
        ) or bool(flags.get("hazard_lab_force_trap_disarm_failure", False))
        if force_failure:
            _mark_act2_trap_revealed(flags)
            _mark_act2_disarm_attempt(flags, actor_id=actor_id, success=False)
            trigger_logs = _trigger_trap_entity(
                trap_id=target_id,
                trap=trap,
                entities=entities,
                map_data=map_data,
                trigger_actor_id=actor_id,
                flags=flags,
                environment_objects=environment_objects,
                affected_actor_ids=["player"],
            )
            journal_events = [
                f"[陷阱解除失败] scout -> {target_id}",
                f"🧰 [解除陷阱] {actor_name} 的工具划过 {trap_name} 的触发簧片，毒气喷口猛然打开。",
            ] + trigger_logs
            _apply_scout_memory_echo_journal(
                state=state,
                flags=flags,
                journal_events=journal_events,
            )
            return {
                "journal_events": journal_events,
                "entities": entities,
                "environment_objects": environment_objects,
                "flags": flags,
                "map_data": map_data,
                "raw_roll_data": _build_action_result(
                    intent="DISARM",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="FORCED_FAILURE_TRIGGERED",
                    extra={
                        "deterministic": True,
                        "reason": "hazard_lab_force_trap_disarm_failure",
                    },
                ),
            }

        trap["status"] = "disabled"
        trap["is_hidden"] = False
        trap["hp"] = 0
        flags["hazard_lab_poison_trap_disarmed"] = True
        flags["hazard_lab_poison_trap_revealed"] = True
        _mark_act2_trap_revealed(flags)
        _mark_act2_disarm_attempt(flags, actor_id=actor_id, success=True)
        _mark_act2_trap_disarmed(flags)
        _sync_lab_trap_state_to_environment(
            environment_objects=environment_objects,
            trap_id=target_id,
            status="disabled",
            is_hidden=False,
        )
        entities.pop(target_id, None)
        _remove_trap_obstacle_from_map(map_data=map_data, x=trap_x, y=trap_y)
        journal_events = [
            f"[陷阱解除] scout -> {target_id}",
            f"🔧 [解除陷阱] {actor_name} 稳稳拆除了 {trap_name}。",
        ]
        _apply_scout_memory_echo_journal(
            state=state,
            flags=flags,
            journal_events=journal_events,
        )
        return {
            "journal_events": journal_events,
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="SUCCESS",
                extra={"deterministic": True, "reason": "hazard_lab_scout_trap_disarm"},
            ),
        }

    disarm_dc = max(1, _coerce_int(trap.get("disarm_dc"), 15))
    dex_mod = calculate_ability_modifier(_get_ability_score(actor, "DEX", 10))
    result = roll_d20(
        dc=disarm_dc,
        modifier=dex_mod,
        roll_type="normal",
    )
    raw_roll = _coerce_int(result.get("raw_roll"), 0)
    total = _coerce_int(result.get("total"), raw_roll + dex_mod)
    is_success = bool(result.get("is_success", False))

    if raw_roll == 1:
        if _is_hazard_lab_map(state) and _normalize_entity_id(target_id) == "gas_trap_1":
            _mark_act2_trap_revealed(flags)
            _mark_act2_disarm_attempt(flags, actor_id=actor_id, success=False)
        trigger_logs = _trigger_trap_entity(
            trap_id=target_id,
            trap=trap,
            entities=entities,
            map_data=map_data,
            trigger_actor_id=actor_id,
            flags=flags,
            environment_objects=environment_objects,
        )
        return {
            "journal_events": [
                f"💥 [解除陷阱] 大失败！{actor_name} 在拆除 {target_name} 时手一抖，引爆了陷阱。"
            ] + trigger_logs,
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="CRITICAL_FAIL_TRIGGERED",
                extra={
                    "dc": disarm_dc,
                    "raw_roll": raw_roll,
                    "total": total,
                    "modifier": dex_mod,
                },
            ),
        }

    if is_success:
        trap_name = _display_entity_name(trap, target_id)
        trap["status"] = "disabled"
        trap["is_hidden"] = False
        trap["hp"] = 0
        if _is_hazard_lab_map(state) and _normalize_entity_id(target_id) == "gas_trap_1":
            flags["hazard_lab_poison_trap_disarmed"] = True
            flags["hazard_lab_poison_trap_revealed"] = True
            _mark_act2_trap_revealed(flags)
            _mark_act2_disarm_attempt(flags, actor_id=actor_id, success=True)
            _mark_act2_trap_disarmed(flags)
            _sync_lab_trap_state_to_environment(
                environment_objects=environment_objects,
                trap_id=target_id,
                status="disabled",
                is_hidden=False,
            )
        entities.pop(target_id, None)
        _remove_trap_obstacle_from_map(map_data=map_data, x=trap_x, y=trap_y)
        return {
            "journal_events": [
                f"🔧 [检定成功] {actor_name} 成功解除了 {trap_name} (1d20: {total} vs DC {disarm_dc})。"
            ],
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="SUCCESS",
                extra={
                    "dc": disarm_dc,
                    "raw_roll": raw_roll,
                    "total": total,
                    "modifier": dex_mod,
                },
            ),
        }

    if _is_hazard_lab_map(state) and _normalize_entity_id(target_id) == "gas_trap_1":
        _mark_act2_trap_revealed(flags)
        _mark_act2_disarm_attempt(flags, actor_id=actor_id, success=False)
        trigger_logs = _trigger_trap_entity(
            trap_id=target_id,
            trap=trap,
            entities=entities,
            map_data=map_data,
            trigger_actor_id=actor_id,
            flags=flags,
            environment_objects=environment_objects,
        )
        return {
            "journal_events": [
                f"🧰 [解除陷阱] {actor_name} 未能拆除 {target_name} (1d20: {total} vs DC {disarm_dc})，机关被触发。"
            ] + trigger_logs,
            "entities": entities,
            "environment_objects": environment_objects,
            "flags": flags,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="DISARM",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="FAIL_TRIGGERED",
                extra={
                    "dc": disarm_dc,
                    "raw_roll": raw_roll,
                    "total": total,
                    "modifier": dex_mod,
                },
            ),
        }
    return {
        "journal_events": [
            f"🧰 [解除陷阱] {actor_name} 未能拆除 {target_name} (1d20: {total} vs DC {disarm_dc})。"
        ],
        "entities": entities,
        "environment_objects": environment_objects,
        "flags": flags,
        "map_data": map_data,
        "raw_roll_data": _build_action_result(
            intent="DISARM",
            actor=actor_id,
            target=target_id,
            is_success=False,
            result_type="FAIL",
            extra={
                "dc": disarm_dc,
                "raw_roll": raw_roll,
                "total": total,
                "modifier": dex_mod,
            },
        ),
    }


def execute_unlock_action(state: Any) -> Dict[str, Any]:
    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    map_data = (
        copy.deepcopy(state.get("map_data"))
        if isinstance(state.get("map_data"), dict)
        else {}
    )
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_entity_id(intent_context.get("action_actor", "player")) or "player"
    target_query = str(intent_context.get("action_target", "") or "").strip()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent="UNLOCK",
        actor_id=actor_id,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock

    target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )
    if not target_id:
        return {
            "journal_events": ["❌ [开锁] 开锁失败：未指定目标。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target="",
                is_success=False,
                result_type="INVALID_TARGET",
            ),
        }
    if not isinstance(target_obj, dict):
        return {
            "journal_events": [f"❌ [开锁] 开锁失败：找不到目标 {target_id}。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="NOT_FOUND",
            ),
        }

    actor = _ensure_actor_entity(actor_id=actor_id, entities=entities, state=state)
    actor_name = _display_entity_name(actor, actor_id)
    actor_x = _coerce_int(actor.get("x"), 4)
    actor_y = _coerce_int(actor.get("y"), 9)
    target_x = _coerce_int(target_obj.get("x"), actor_x)
    target_y = _coerce_int(target_obj.get("y"), actor_y)
    distance = _chebyshev_distance(
        actor_x=actor_x,
        actor_y=actor_y,
        target_x=target_x,
        target_y=target_y,
    )
    if distance > 1:
        return {
            "journal_events": [f"❌ [开锁] {target_name} 距离过远，需相邻才能开锁。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="OUT_OF_RANGE",
                extra={"distance": distance},
            ),
        }

    is_locked = bool(target_obj.get("is_locked", False)) or str(target_obj.get("status", "")).strip().lower() == "locked"
    if not is_locked:
        return {
            "journal_events": [f"🔓 [开锁] {target_name} 已经是开启状态。"],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="ALREADY_UNLOCKED",
            ),
        }

    if _is_hazard_lab_map(state) and _is_lab_corridor_door(target_id):
        flags = dict(state.get("flags") or {})
        _mark_lab_corridor_door_base_flags(flags)
        if not _is_explicit_lab_lockpick_attempt(state, intent_context if isinstance(intent_context, dict) else {}):
            _mark_secret_study_hint(flags)
            return {
                "journal_events": [
                    "🚪 [系统] 实验室重门需要 lab_key；也可以明确尝试 DC 15 撬锁。",
                    "🕯️ [线索] 门框附近有冷风，旁边墙面传来空响；附近可能还有通往书房的入口。",
                ],
                "entities": entities,
                "environment_objects": environment_objects,
                "map_data": map_data,
                "flags": flags,
                "raw_roll_data": _build_action_result(
                    intent="UNLOCK",
                    actor=actor_id,
                    target=target_id,
                    is_success=False,
                    result_type="INSPECT_REQUIRES_EXPLICIT_LOCKPICK",
                    extra={"key_required": "lab_key", "lockpick_dc": _coerce_int(target_obj.get("lockpick_dc"), 15)},
                ),
            }
        flags["act2_corridor_exit_lockpick_attempted"] = True
        lockpick_dc = max(
            1,
            _coerce_int(
                target_obj.get("lockpick_dc"),
                _coerce_int(target_obj.get("unlock_dc"), _coerce_int(intent_context.get("difficulty_class"), 15)),
            ),
        )
        dex_mod = calculate_ability_modifier(_get_ability_score(actor, "DEX", 10))
        force_success = bool(intent_context.get("force_lockpick_success", False)) or bool(
            flags.get("hazard_lab_force_lockpick_success", False)
        )
        force_failure = bool(intent_context.get("force_lockpick_failure", False)) or bool(
            flags.get("hazard_lab_force_lockpick_failure", False)
        )
        if force_success:
            raw_roll = max(1, min(20, lockpick_dc - dex_mod))
            total = raw_roll + dex_mod
            is_success = True
        elif force_failure:
            raw_roll = 1
            total = raw_roll + dex_mod
            is_success = False
        else:
            result = roll_d20(
                dc=lockpick_dc,
                modifier=dex_mod,
                roll_type="normal",
            )
            raw_roll = _coerce_int(result.get("raw_roll"), 0)
            total = _coerce_int(result.get("total"), raw_roll + dex_mod)
            is_success = bool(result.get("is_success", False))

        if is_success:
            target_obj["is_locked"] = False
            target_obj["is_open"] = True
            target_obj["status"] = "open"
            _sync_object_state(
                entities=entities,
                environment_objects=environment_objects,
                target_id=target_id,
                updates={"is_locked": False, "is_open": True, "status": "open"},
            )
            _sync_door_state_to_map(map_data=map_data, entities=entities)
            flags["act2_corridor_exit_lockpick_success"] = True
            flags["act2_lockpick_success_route_to_boss"] = True
            return {
                "journal_events": [
                    f"🔓 [撬锁成功] {actor_name} 撬开了通往实验室的重门 (1d20: {total} vs DC {lockpick_dc})。"
                ],
                "entities": entities,
                "environment_objects": environment_objects,
                "map_data": map_data,
                "flags": flags,
                "raw_roll_data": _build_action_result(
                    intent="UNLOCK",
                    actor=actor_id,
                    target=target_id,
                    is_success=True,
                    result_type="SUCCESS",
                    extra={
                        "dc": lockpick_dc,
                        "raw_roll": raw_roll,
                        "total": total,
                        "modifier": dex_mod,
                        "route": "boss_room_direct",
                    },
                ),
            }

        flags["act2_corridor_exit_lockpick_success"] = False
        flags["act2_lockpick_success_route_to_boss"] = False
        _mark_secret_study_hint(flags)
        return {
            "journal_events": [
                f"🔒 [撬锁失败] {actor_name} 没能撬开实验室重门 (1d20: {total} vs DC {lockpick_dc})。",
                "🕯️ [线索] 墙后有空响，也许附近还有密道或别的入口。",
            ],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "flags": flags,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target=target_id,
                is_success=False,
                result_type="FAIL_SECRET_STUDY_HINT",
                extra={
                    "dc": lockpick_dc,
                    "raw_roll": raw_roll,
                    "total": total,
                    "modifier": dex_mod,
                    "route": "secret_study_hint",
                },
            ),
        }

    unlock_dc = max(
        1,
        _coerce_int(
            target_obj.get("unlock_dc"),
            _coerce_int(intent_context.get("difficulty_class"), 14),
        ),
    )
    dex_mod = calculate_ability_modifier(_get_ability_score(actor, "DEX", 10))
    result = roll_d20(
        dc=unlock_dc,
        modifier=dex_mod,
        roll_type="normal",
    )
    raw_roll = _coerce_int(result.get("raw_roll"), 0)
    total = _coerce_int(result.get("total"), raw_roll + dex_mod)
    is_success = bool(result.get("is_success", False))

    if is_success:
        target_obj["is_locked"] = False
        status = str(target_obj.get("status", "")).strip().lower()
        if status == "locked":
            target_obj["status"] = "opened"
        payload = {
            "journal_events": [
                f"🔓 [检定成功] {actor_name} 成功打开了 {target_name} (1d20: {total} vs DC {unlock_dc})。"
            ],
            "entities": entities,
            "environment_objects": environment_objects,
            "map_data": map_data,
            "raw_roll_data": _build_action_result(
                intent="UNLOCK",
                actor=actor_id,
                target=target_id,
                is_success=True,
                result_type="SUCCESS",
                extra={
                    "dc": unlock_dc,
                    "raw_roll": raw_roll,
                    "total": total,
                    "modifier": dex_mod,
                },
            ),
        }
        return payload

    return {
        "journal_events": [
            f"🔒 [开锁失败] {actor_name} 未能打开 {target_name} (1d20: {total} vs DC {unlock_dc})。"
        ],
        "entities": entities,
        "environment_objects": environment_objects,
        "map_data": map_data,
        "raw_roll_data": _build_action_result(
            intent="UNLOCK",
            actor=actor_id,
            target=target_id,
            is_success=False,
            result_type="FAIL",
            extra={
                "dc": unlock_dc,
                "raw_roll": raw_roll,
                "total": total,
                "modifier": dex_mod,
            },
        ),
    }


# -----------------------------------------------------------------------------
# 技能检定类型与属性映射
# -----------------------------------------------------------------------------
# 支持的检定类型：PERSUASION(劝说), DECEPTION(欺瞒), STEALTH(隐匿), INSIGHT(洞悉),
# INTIMIDATION(威吓), ATTACK(攻击), STEAL(偷窃), ACTION(通用动作)。
# 每种检定映射到 d20 5e 属性，用于后续扩展（如玩家属性修正）。
# -----------------------------------------------------------------------------

SKILL_CHECK_TYPES = (
    "PERSUASION",
    "DECEPTION",
    "STEALTH",
    "INTIMIDATION",
    "INSIGHT",
    "ATTACK",
    "CAST_SPELL",
    "LOOT",
    "STEAL",
    "ACTION",
    "PERCEPTION",
    "INVESTIGATION",
    "SLEIGHT_OF_HAND",
    "DISARM",
    "UNLOCK",
    "ATHLETICS",
    "USE_ITEM",
    "CONSUME",
    "EQUIP",
    "UNEQUIP",
    "MOVE",
    "APPROACH",
    "INTERACT",
    "SHOVE",
)


def get_ability_for_action(action_type: str) -> str:
    """
    将检定类型映射到 d20 5e 属性。
    """
    key = str(action_type or "").strip().upper()
    action_to_ability = {
        "PERSUASION": "CHA",
        "DECEPTION": "CHA",
        "INTIMIDATION": "CHA",
        "STEALTH": "DEX",
        "INSIGHT": "WIS",
        "PERCEPTION": "WIS",
        "INVESTIGATION": "INT",
        "SLEIGHT_OF_HAND": "DEX",
        "DISARM": "DEX",
        "UNLOCK": "DEX",
        "ATHLETICS": "STR",
        "ATTACK": "STR",
        "CAST_SPELL": "WIS",
        "LOOT": "DEX",
        "STEAL": "DEX",
        "USE_ITEM": "DEX",
        "CONSUME": "CON",
        "EQUIP": "DEX",
        "UNEQUIP": "DEX",
        "MOVE": "DEX",
        "APPROACH": "DEX",
        "INTERACT": "DEX",
        "SHOVE": "STR",
        "ACTION": "CHA",
        "NONE": "CHA",
    }
    return action_to_ability.get(key, "CHA")


def get_player_modifier(player_data: dict, ability_name: str) -> Optional[int]:
    """
    Get player's ability modifier for a given ability.
    
    Args:
        player_data: Player profile dictionary containing ability_scores
        ability_name: Ability score abbreviation (STR, DEX, CON, INT, WIS, CHA)
    
    Returns:
        Optional[int]: Ability modifier, or None if ability not found
    """
    if player_data is None:
        return None
    
    ability_scores = player_data.get('ability_scores', {})
    if ability_name not in ability_scores:
        return None
    
    ability_score = ability_scores[ability_name]
    return calculate_ability_modifier(ability_score)


def determine_roll_type(action_type: str, relationship_score: int) -> str:
    """
    Determine roll type (normal/advantage/disadvantage) based on action and relationship.
    
    Args:
        action_type: The action type from DM analysis (e.g., "PERSUASION", "DECEPTION")
        relationship_score: Current relationship score with the NPC
    
    Returns:
        str: 'normal', 'advantage', or 'disadvantage'
    """
    # Advantage: PERSUASION or DECEPTION with high relationship (>= 30)
    if action_type in ["PERSUASION", "DECEPTION"] and relationship_score >= 30:
        return 'advantage'
    
    # Disadvantage: Low relationship (<= -20)
    if relationship_score <= -20:
        return 'disadvantage'
    
    return 'normal'


# -----------------------------------------------------------------------------
# 好感度与掷骰（仅修正骰子，不自动改 affection）
# -----------------------------------------------------------------------------
#
# PERSUASION/DECEPTION 时按 affection 每 20 点 ±1 骰子修正；advantage/disadvantage 亦由 affection 阈值决定。
# 检定成败不再扣减 affection——情感波动由 DM 分析与 NPC/LLM 输出自行表达。
# 动态 DC 来自 intent_context["difficulty_class"]，掷骰明细写入 journal_events。
# -----------------------------------------------------------------------------


def calculate_relationship_modifier(relationship: int, action_type: str) -> int:
    """
    根据好感度计算骰子修正值。仅对 PERSUASION、DECEPTION 生效。
    
    规则：好感度每 20 点，骰子点数 +1（向下取整）。负好感同理（每 -20 点 -1）。
    
    Args:
        relationship: 当前 relationship 分数 (-100..100)
        action_type: 检定类型，仅 PERSUASION/DECEPTION 会应用此修正
    
    Returns:
        int: 修正值，如 relationship=40 且 PERSUASION 则返回 2
    """
    if action_type not in ("PERSUASION", "DECEPTION"):
        return 0
    return relationship // 20


# -----------------------------------------------------------------------------
# 意图(How)与话题(What)分离 —— 彻底解决 LLM 分类冲突
# -----------------------------------------------------------------------------
#
# 【问题】若将 PROBE_SECRET 混入 action_type，会出现维度冲突：
# - 玩家「用劝说口吻刺探神器」→ PERSUASION 还是 PROBE_SECRET？LLM 难以二选一。
# - 玩家「边撒谎边问未知协议立场」→ DECEPTION 与刺探话题同时成立，单维分类必然丢失信息。
#
# 【方案】action_type 保持纯粹的机制动作（How：如何互动），is_probing_secret 独立表示
# 话题标签（What：是否触碰核心隐私）。两者正交，LLM 可同时输出：
# - action_type=PERSUASION, is_probing_secret=true → 用劝说方式刺探
# - action_type=DECEPTION, is_probing_secret=true → 用欺瞒方式刺探
#
# 【V2】is_probing_secret 仅作 DM 标签写入 state；检定始终走正常骰子，叙事由 LLM + story_rules 决定。
# -----------------------------------------------------------------------------


def execute_skill_check(state: Any) -> Dict[str, Any]:
    """
    执行技能检定，返回客观掷骰结果。支持多角色的属性提取（intent_context.action_actor）。
    """
    intent_raw = state.get("intent", "ACTION")
    intent = str(intent_raw).strip().upper() if intent_raw else "ACTION"
    intent_context = state.get("intent_context") or {}
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    entities = copy.deepcopy(state.get("entities") or {})
    target_query = str(intent_context.get("action_target", "") or "").strip()
    actual_target_id, target_obj, target_name = _resolve_target_reference(
        target_id=target_query,
        entities=entities,
        environment_objects=environment_objects,
    )

    action_actor = str(intent_context.get("action_actor", "player") or "player").strip().lower()
    turn_lock = _reject_if_not_active_turn(
        state=state,
        intent=intent,
        actor_id=action_actor,
        target_id=target_query,
        entities=entities,
    )
    if turn_lock:
        return turn_lock
    approach_events: List[str] = []
    if intent in {"SLEIGHT_OF_HAND", "ACTION", "ATHLETICS", "UNLOCK", "DISARM"} and isinstance(target_obj, dict):
        approach_events = _auto_approach_actor_to_target(
            entities=entities,
            state=state,
            actor_id=action_actor,
            target=target_obj,
            target_name=target_name,
        )

    _entities_map = state.get("entities") or {}
    if not isinstance(_entities_map, dict):
        _entities_map = {}

    _speaker_fb = next(iter(_entities_map), None) if _entities_map else None
    speaker = (state.get("current_speaker") or "").strip().lower() or (_speaker_fb or "unknown")
    affection = (_entities_map.get(speaker, {}) or {}).get("affection", 0)

    dc = intent_context.get("difficulty_class")
    if dc is None or (isinstance(dc, (int, float)) and dc <= 0):
        dc = 12
    dc = int(dc)

    ability_name = get_ability_for_action(intent)
    stat_mod = 0

    if action_actor != "player":
        try:
            from characters.loader import load_character

            char = load_character(action_actor)
            score = (char.data.get("ability_scores") or {}).get(ability_name, 10)
            stat_mod = calculate_ability_modifier(int(score))
        except Exception as e:
            print(f"⚠️ 无法读取 {action_actor} 的属性，默认修正为 +0 ({e})")
            stat_mod = 0
    else:
        # 玩家执行：未来可从 player.json / entities['player'] 读取；当前暂定 +2 作为熟练补偿占位
        stat_mod = 2

    rel_mod = calculate_relationship_modifier(affection, intent)
    modifier = stat_mod + rel_mod

    roll_type = determine_roll_type(intent, affection)
    result = roll_d20(dc=dc, modifier=modifier, roll_type=roll_type)

    rolls_str = str(result.get("rolls", [result.get("raw_roll", "?")]))
    total = result.get("total", 0)
    result_type = result.get("result_type")
    result_val = result_type.value if result_type is not None and hasattr(result_type, "value") else str(result_type)

    actor_display = action_actor.capitalize()
    journal_lines = [
        f"Skill Check | {actor_display} uses {intent} ({ability_name}) | DC {dc} | "
        f"Roll {rolls_str} + {modifier:+d} = {total} vs DC {dc} | "
        f"Result: {result_val}",
    ] + approach_events
    if DEBUG_ALWAYS_PASS_CHECKS:
        journal_lines.append("  [DEV MODE] 自动大成功")
    if stat_mod != 0:
        journal_lines.append(f"  [Attribute modifier ({ability_name}): {stat_mod:+d}]")
    if rel_mod != 0:
        journal_lines.append(f"  [Affection modifier: {rel_mod:+d} (affection={affection})]")

    payload: Dict[str, Any] = {
        "journal_events": journal_lines,
        "entities": entities,
        "raw_roll_data": {
            "intent": intent,
            "actor": action_actor,
            "target": actual_target_id,
            "dc": dc,
            "modifier": modifier,
            "result": result,
        },
    }

    if _is_unlockable_skill_success(
        intent=intent,
        target_obj=target_obj,
        result=result,
    ):
        target_id = actual_target_id
        if target_id in environment_objects and isinstance(environment_objects.get(target_id), dict):
            target_obj = environment_objects[target_id]
            target_obj["status"] = "opened"
            target_obj["is_locked"] = False
            payload["environment_objects"] = environment_objects
        elif target_id in entities and isinstance(entities.get(target_id), dict):
            target_obj = entities[target_id]
            target_obj["status"] = "opened"
            target_obj["is_locked"] = False
            payload["entities"] = entities
        target_name = str(
            target_obj.get("name") if isinstance(target_obj, dict) else target_id.replace("_", " ").title()
        )
        journal_lines.append(f"🔓 [场景交互] 随着咔哒一声，{target_name} 被解锁了！")
    return payload


def calculate_passive_dc(action_type: str, npc_attributes: dict) -> Optional[int]:
    """
    Calculate passive DC based on NPC's stats (Phase 1: Rules Overrule).
    
    This function calculates the DC that the player must beat based on the NPC's
    actual ability scores, overriding the DM AI's DC estimate.
    
    Args:
        action_type: The action type from DM analysis (e.g., "PERSUASION", "DECEPTION")
        npc_attributes: NPC character attributes dictionary containing ability_scores
    
    Returns:
        Optional[int]: Calculated DC if applicable, None to use DM's default DC
    """
    # Get NPC's WIS modifier
    ability_scores = npc_attributes.get('ability_scores', {})
    wis_score = ability_scores.get('WIS', 10)
    wis_mod = calculate_ability_modifier(wis_score)
    
    # Calculate passive DC based on action type
    if action_type == "DECEPTION":
        # Passive Insight: 10 + WIS modifier (detecting lies)
        return 10 + wis_mod
    elif action_type == "PERSUASION":
        # Passive Insight/Skepticism: 10 + WIS modifier (judging honesty)
        return 10 + wis_mod
    elif action_type == "INTIMIDATION":
        # Passive Willpower: 10 + WIS modifier (resisting threats)
        return 10 + wis_mod
    else:
        # For other action types, use DM's default DC
        return None


def check_condition(condition_str: str, flags: dict) -> bool:
    """
    Safely evaluate a simple condition string against flags.
    
    Supports formats like: "flags.some_flag == True"
    Returns True for empty/None conditions.
    Handles "True" as a special case (always returns True).
    """
    if not condition_str or not condition_str.strip():
        return True
    
    # Handle "True" as a special case (always active conditions)
    if str(condition_str).strip() == "True":
        return True
    
    condition = condition_str.strip()
    operator = None
    if "==" in condition:
        lhs, rhs = condition.split("==", 1)
        operator = "=="
    elif "!=" in condition:
        lhs, rhs = condition.split("!=", 1)
        operator = "!="
    else:
        return False
    
    lhs = lhs.strip()
    rhs = rhs.strip()
    if not lhs.startswith("flags."):
        return False
    
    key = lhs[len("flags."):].strip()
    if not key:
        return False
    
    try:
        rhs_value = ast.literal_eval(rhs)
    except Exception:
        rhs_value = rhs.strip('"').strip("'")
    
    current_value = flags.get(key)
    if operator == "==":
        return current_value == rhs_value
    return current_value != rhs_value


def update_flags(effect_str: str, flags: dict) -> dict:
    """
    Apply a flag update string to the flags dict in place.
    
    Supports formats like: "flags.some_flag = True"
    """
    if not effect_str or not effect_str.strip():
        return flags
    
    effect = effect_str.strip()
    if "=" not in effect:
        return flags
    
    lhs, rhs = effect.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()
    if not lhs.startswith("flags."):
        return flags
    
    key = lhs[len("flags."):].strip()
    if not key:
        return flags
    
    try:
        rhs_value = ast.literal_eval(rhs)
    except Exception:
        rhs_value = rhs.strip('"').strip("'")

    old_value = flags.get(key)
    flags[key] = rhs_value
    if old_value != rhs_value:
        print(f"[flags] {key}: {old_value} -> {rhs_value}")
    return flags


def get_situational_bonus(
    history: list,
    action_type: str,
    rules_config: list,
    flags: dict,
    current_message: str = ""
) -> tuple[int, str]:
    """
    Calculate situational bonus based on conversation context (Data-Driven Rules).
    
    This function checks the current user message (and optionally history) for keywords
    defined in rules_config that indicate shared context or past bonds, which grant bonuses
    to social skill checks.
    
    Args:
        history: List of conversation history dicts with 'role' and 'content' keys
        action_type: The action type from DM analysis (e.g., "PERSUASION", "DECEPTION")
        rules_config: List of situational bonus rules loaded from config
        flags: Persistent world-state flags dictionary
        current_message: The current user input message (optional, checked first)
    
    Returns:
        tuple[int, str]: (bonus, reason) - bonus amount and explanation
    """
    # Check current message first, then fall back to last message in history
    message_to_check = current_message
    
    if not message_to_check:
        # Get the last user message from history
        for msg in reversed(history):
            if msg.get('role') == 'user':
                message_to_check = msg.get('content', '')
                break
    
    if not message_to_check:
        return (0, "")
    
    # Convert to lowercase for matching
    message_lower = message_to_check.lower()
    
    total_bonus = 0
    reasons = []
    
    for rule in rules_config or []:
        condition = rule.get("condition")
        if not check_condition(condition, flags):
            continue
        
        applicable_actions = rule.get("applicable_actions", [])
        if "ALL" not in applicable_actions and action_type not in applicable_actions:
            continue
        
        trigger_type = rule.get("trigger_type")
        if trigger_type == "keyword_match":
            keywords = rule.get("keywords", [])
            if any(keyword in message_lower for keyword in keywords):
                total_bonus += rule.get("bonus_value", 0)
                description = rule.get("description")
                if description:
                    reasons.append(description)
    
    return (total_bonus, ", ".join(reasons))


# -----------------------------------------------------------------------------
# 对话触发器：对话即交互（Dialogue as Interaction）
# -----------------------------------------------------------------------------
#
# 【AI Narrative Engineer 与叙事一致性】
# 在叙事驱动游戏中，玩家的「对话」不应只是文本输出，而应能直接推动世界状态：
# 说「给你药水」即完成物品转移，说「我发现了秘密」即解锁剧情 flag。这样：
# 1) 叙事与机制一致：对话内容与后续剧情/背包/好感度严格同步，避免「说了不算」的割裂；
# 2) 下一轮生成有据可查：所有触发的剧情事件写入 journal_events，LLM 在 [RECENT MEMORIES]
#    中能看到「刚刚发生的重大转折」，从而生成连贯的后续反应；
# 3) 好感度与关键行为绑定：通过触发器配置中的 approval_change，将「给予剧情物品」等
#    行为直接映射为 relationship 变化，使数值与叙事选择一致。
#
# 调用方（如 generation_node）须将本函数对 flags / player_inv / npc_inv 的原地修改
# 写回 state，并合并返回的 journal_entries、relationship_delta，以保持全局状态一致。
# -----------------------------------------------------------------------------


def process_dialogue_triggers(
    user_input: str,
    triggers_config: list,
    flags: dict,
    ui=None,
    player_inv=None,
    npc_inv=None,
) -> Dict[str, Any]:
    """
    根据玩家输入匹配对话触发器，执行效果并返回需合并进 state 的结果。
    
    **触发后果（增强）**：
    - **Flags**：按 effects 中的 "flags.xxx = value" 原地修改传入的 flags，调用方须将
      同一 dict 写回 state["flags"]。
    - **背包**：通过 inventory.give:item_id 从 player_inv 移除、向 npc_inv 增加，实现
      「对话即物品转移」；调用方须将修改后的 player_inv.to_dict() / npc_inv.to_dict()
      写回 state["player_inventory"] 与 state["npc_inventory"]，确保 Generation 下一轮
      能基于最新背包生成（避免幻觉）。
    
    **好感度**：触发器配置可含 approval_change（整数）。所有在本轮匹配的触发器的
    approval_change 会累加，通过返回值 relationship_delta 交给调用方加算到
    state["relationship"]，实现「剧情物品转交」等行为直接影响关系分数。
    
    **日志**：每个被触发的触发器都会产生一条 journal 条目（优先用 system_message，
    否则用 trigger id / description），通过返回值 journal_entries 交给调用方合并进
    state["journal_events"]，保证下一轮对话中 [RECENT MEMORIES] 能引用这些重大转折。
    
    Args:
        user_input: 当前玩家输入文本。
        triggers_config: YAML 中的 dialogue_triggers 列表，每项可含：
            - trigger_type, keywords, effects
            - system_message: 写入 journal 的剧情描述（可选）
            - approval_change: 本触发对 relationship 的加减值（可选，默认 0）
        flags: 世界状态 flag 字典，**原地修改**。
        ui: 可选 UI，用于打印转移结果等。
        player_inv: 可选玩家背包对象（Inventory），**原地修改**（转移时 remove）。
        npc_inv: 可选 NPC 背包对象（Inventory），**原地修改**（转移时 add）。
    
    Returns:
        dict:
            - journal_entries: list[str]，本轮触发的剧情事件，应合并进 state["journal_events"]；
            - relationship_delta: int，本轮触发器带来的 relationship 变化总和，应加算到 state["relationship"]。
    """
    if not user_input or not triggers_config:
        return {"journal_entries": [], "relationship_delta": 0}

    message_lower = user_input.lower()
    journal_entries: List[str] = []
    relationship_delta = 0

    for trigger in triggers_config:
        trigger_type = trigger.get("trigger_type")
        if trigger_type != "keyword_match":
            continue

        keywords = trigger.get("keywords", [])
        if not any(keyword.lower() in message_lower for keyword in keywords):
            continue

        # ---------- 本触发器已匹配：执行效果（直接操作 flags 与背包）----------
        effects = trigger.get("effects", [])
        for effect_str in effects:
            # 更新世界状态 flag，调用方将同一 flags 写回 state["flags"]
            if "flags." in effect_str:
                update_flags(effect_str, flags)
            # 物品转移：直接修改 player_inv / npc_inv，调用方须将 to_dict() 写回 state
            elif effect_str.startswith("inventory.give:"):
                item_id = effect_str.split(":", 1)[1].strip()
                if player_inv and npc_inv:
                    from core.systems.inventory import get_registry
                    registry = get_registry()
                    item_name = registry.get_name(item_id)
                    if player_inv.remove(item_id):
                        npc_inv.add(item_id)
                        if ui:
                            ui.print_system_info(f"🎒 Item Transferred: {item_name} (Player -> NPC)")
                    else:
                        if ui:
                            ui.print_system_info(f"❌ Transaction Failed: You don't have {item_name}")

        # 好感度：配置中的 approval_change 累加，由调用方加算到 state["relationship"]
        delta = trigger.get("approval_change", 0)
        if isinstance(delta, int):
            relationship_delta += delta

        # 日志：每条触发都生成一条 journal，确保下一轮 [RECENT MEMORIES] 可见
        system_message = trigger.get("system_message")
        if system_message:
            journal_entries.append(system_message)
        else:
            trigger_id = trigger.get("id", "unknown")
            desc = trigger.get("description", "")
            journal_entries.append(f"[Story Trigger] {trigger_id}: {desc or 'triggered'}")

    return {"journal_entries": journal_entries, "relationship_delta": relationship_delta}


def update_npc_state(current_status: str, duration: int) -> tuple[str, int]:
    """
    Update NPC state by decrementing duration and resetting to NORMAL if needed.
    
    Args:
        current_status: Current NPC status ("NORMAL", "SILENT", "VULNERABLE")
        duration: Current duration (number of turns remaining)
    
    Returns:
        tuple[str, int]: (new_status, new_duration)
    """
    if duration <= 0:
        return ("NORMAL", 0)
    
    new_duration = duration - 1
    if new_duration <= 0:
        return ("NORMAL", 0)
    
    return (current_status, new_duration)


# =========================================
# Item Effect Logic (Data-Driven)
# =========================================

def parse_dice_string(dice_str: str) -> int:
    """
    Parse generic dice strings like '2d4+2', '1d8', or fixed numbers '5'.
    Returns the calculated result.
    """
    # 1. Fixed number
    if str(dice_str).isdigit():
        return int(dice_str)

    # 2. Dice formula: XdY(+/-)Z
    match = re.match(r'(\d+)d(\d+)(?:([+-])(\d+))?', dice_str)
    if not match:
        return 0

    num_dice = int(match.group(1))
    sides = int(match.group(2))
    operator = match.group(3)
    modifier = int(match.group(4)) if match.group(4) else 0

    total = sum(random.randint(1, sides) for _ in range(num_dice))

    if operator == '-':
        total -= modifier
    else:
        total += modifier

    return total


def apply_item_effect(item_id: str, item_data: dict) -> dict:
    """
    Executes the effect defined in the item's YAML configuration.

    Args:
        item_id: The ID of the item (e.g., 'healing_potion')
        item_data: The dictionary from items.yaml (contains 'effect', 'name', etc.)

    Returns:
        dict: Result of the application
        {
            "success": bool,
            "message": str, # Description for UI/Log
            "value": int,   # Numeric value (if applicable, like HP healed)
            "type": str     # Effect type (e.g., 'heal', 'buff')
        }
    """
    effect_str = item_data.get("effect")
    item_name = item_data.get("name", item_id)

    if not effect_str:
        return {
            "success": False,
            "message": f"{item_name} has no usage effect.",
            "value": 0,
            "type": "none"
        }

    # Effect Type 1: Healing (Format: "heal:2d4+2")
    if effect_str.startswith("heal:"):
        dice_formula = effect_str.split(":")[1]
        heal_amount = parse_dice_string(dice_formula)

        return {
            "success": True,
            "message": f"restores {heal_amount} HP.",
            "value": heal_amount,
            "type": "heal"
        }

    # Future Effect Types can be added here (e.g., "buff:strength", "damage:fire")

    # Default fallback
    return {
        "success": True,
        "message": "used successfully.",
        "value": 0,
        "type": "generic"
    }
