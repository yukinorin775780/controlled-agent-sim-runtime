"""
空存档创世：统一生成初始世界状态，供 CLI (main) 与 API (server) 复用。
"""

import copy
import json
import os
from typing import Any, Dict, List, Optional

import yaml
from core.graph.nodes.utils import default_entities
from core.systems.inventory import get_registry
from core.systems.maps import get_map_data


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARTY_ENTITY_IDS = {"player", "analyst", "scout", "tactician"}


def _build_initial_entities(*, use_spawn_table: bool = False) -> Dict[str, Any]:
    """
    基于默认实体表构建初始场景实体，并确保战斗测试怪物存在。
    """
    entities = copy.deepcopy(default_entities)
    if use_spawn_table:
        entities = {
            entity_id: entity_data
            for entity_id, entity_data in entities.items()
            if entity_id in PARTY_ENTITY_IDS
        }
    entities.setdefault(
        "player",
        {
            "name": "玩家",
            "faction": "player",
            "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "speed": 30,
            "hp": 20,
            "max_hp": 20,
            "ac": 10,
            "status": "alive",
            "inventory": {},
            "equipment": {"main_hand": None, "ranged": None, "armor": None},
            "position": "camp_center",
            "x": 4,
            "y": 9,
            "active_buffs": [],
            "status_effects": [],
            "affection": 0,
        },
    )
    if not use_spawn_table:
        entities.setdefault(
            "drone_1",
            {
                "name": "训练无人机",
                "faction": "hostile",
                "ability_scores": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
                "speed": 30,
                "hp": 7,
                "max_hp": 7,
                "ac": 15,
                "status": "alive",
                "inventory": {
                    "gold_coin": 5,
                    "scimitar": 1,
                },
                "equipment": {"main_hand": None, "ranged": None, "armor": None},
                "position": "camp_center",
                "x": 4,
                "y": 3,
                "active_buffs": [],
                "status_effects": [],
                "affection": 0,
            },
        )
        entities.setdefault(
            "drone_sentinel",
            {
                "name": "训练无人机弓箭手",
                "faction": "hostile",
                "ability_scores": {"STR": 8, "DEX": 16, "CON": 10, "INT": 10, "WIS": 9, "CHA": 8},
                "speed": 30,
                "hp": 5,
                "max_hp": 5,
                "ac": 13,
                "status": "alive",
                "inventory": {"gold_coin": 3, "shortbow": 1},
                "equipment": {"main_hand": None, "ranged": "shortbow", "armor": None},
                "position": "camp_center",
                "x": 9,
                "y": 3,
                "active_buffs": [],
                "status_effects": [],
                "affection": 0,
                "enemy_type": "archer",
            },
        )
        entities.setdefault(
            "drone_support",
            {
                "name": "训练无人机萨满",
                "faction": "hostile",
                "ability_scores": {"STR": 8, "DEX": 12, "CON": 10, "INT": 10, "WIS": 14, "CHA": 10},
                "speed": 30,
                "hp": 6,
                "max_hp": 6,
                "ac": 12,
                "status": "alive",
                "inventory": {"gold_coin": 2, "healing_potion": 1},
                "equipment": {"main_hand": "rusty_dagger", "ranged": None, "armor": None},
                "spell_slots": {"level_1": 1},
                "spells": {"cantrips": ["sacred_flame"], "level_1": ["healing_word"]},
                "position": "camp_center",
                "x": 8,
                "y": 4,
                "active_buffs": [],
                "status_effects": [],
                "affection": 0,
                "enemy_type": "shaman",
            },
        )
    scout = entities.get("scout")
    if isinstance(scout, dict):
        equipment = scout.setdefault("equipment", {})
        if isinstance(equipment, dict):
            if not equipment.get("main_hand"):
                equipment["main_hand"] = "rusty_dagger"
            if not equipment.get("ranged"):
                equipment["ranged"] = "shortbow"
            equipment.setdefault("armor", None)
    return entities


def _normalize_inventory(raw_inventory: Any) -> Dict[str, int]:
    if isinstance(raw_inventory, dict):
        inventory: Dict[str, int] = {}
        for item_id, count in raw_inventory.items():
            key = str(item_id or "").strip()
            if not key:
                continue
            try:
                qty = int(count)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            inventory[key] = inventory.get(key, 0) + qty
        return inventory

    if not isinstance(raw_inventory, list):
        return {}

    registry = get_registry()
    inventory = {}
    for item in raw_inventory:
        if isinstance(item, str):
            item_id = registry.resolve_item_id(item) or str(item).strip().lower().replace(" ", "_")
            if not item_id:
                continue
            inventory[item_id] = inventory.get(item_id, 0) + 1
            continue
        if not isinstance(item, dict):
            continue
        raw_item_id = item.get("id")
        item_id = registry.resolve_item_id(raw_item_id) or str(raw_item_id or "").strip().lower().replace(" ", "_")
        if not item_id:
            continue
        try:
            qty = int(item.get("count", 1))
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            continue
        inventory[item_id] = inventory.get(item_id, 0) + qty
    return inventory


def _normalize_equipment(raw_equipment: Any, inventory_data: Dict[str, int]) -> Dict[str, Any]:
    equipment: Dict[str, Any] = {"main_hand": None, "ranged": None, "armor": None}
    registry = get_registry()

    if isinstance(raw_equipment, dict):
        for raw_slot, raw_item in raw_equipment.items():
            slot = str(raw_slot or "").strip().lower()
            if slot == "weapon":
                slot = "main_hand"
            if slot not in equipment:
                continue
            item_id = registry.resolve_item_id(raw_item) or str(raw_item or "").strip().lower().replace(" ", "_")
            if item_id:
                equipment[slot] = item_id
        return equipment

    if isinstance(raw_equipment, list):
        for raw_item in raw_equipment:
            raw_item_id = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
            item_id = registry.resolve_item_id(raw_item_id) or str(raw_item_id or "").strip().lower().replace(" ", "_")
            if not item_id:
                continue
            item_data = registry.get_item_data(item_id)
            slot = str(item_data.get("equip_slot", "")).strip().lower()
            if slot in equipment and equipment.get(slot) is None:
                equipment[slot] = item_id

    if equipment.get("main_hand") is None:
        for item_id in inventory_data.keys():
            item_data = registry.get_item_data(item_id)
            slot = str(item_data.get("equip_slot", "")).strip().lower()
            if slot == "main_hand":
                equipment["main_hand"] = item_id
                break
    return equipment


def _load_prefab_data(prefab_path: str) -> Dict[str, Any]:
    relative_path = str(prefab_path or "").strip()
    if not relative_path:
        return {}
    target_path = relative_path
    if not os.path.isabs(target_path):
        target_path = os.path.join(PROJECT_ROOT, relative_path)
    if not os.path.exists(target_path):
        return {}
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _build_spawned_entity(spawn_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prefab_path = str(spawn_data.get("prefab") or "").strip()
    instance_id = str(spawn_data.get("instance_id") or "").strip()
    if not prefab_path or not instance_id:
        return None

    prefab_data = copy.deepcopy(_load_prefab_data(prefab_path))
    if not isinstance(prefab_data, dict):
        return None

    base_stats = prefab_data.get("base_stats") or {}
    attributes = prefab_data.get("attributes") or {}
    combat = prefab_data.get("combat") or {}
    ability_scores = (
        prefab_data.get("ability_scores")
        or attributes.get("ability_scores")
        or {}
    )

    raw_inventory = prefab_data.get("inventory")
    if raw_inventory is None:
        raw_inventory = attributes.get("inventory")
    inventory = _normalize_inventory(raw_inventory)
    raw_equipment = prefab_data.get("equipment")
    if raw_equipment is None:
        raw_equipment = base_stats.get("equipment")
    equipment = _normalize_equipment(raw_equipment, inventory)

    max_hp_raw = prefab_data.get("max_hp", base_stats.get("max_hp", prefab_data.get("hp", base_stats.get("hp", combat.get("hit_points", 10)))))
    try:
        max_hp = max(1, int(max_hp_raw))
    except (TypeError, ValueError):
        max_hp = 10
    hp_raw = prefab_data.get("hp", base_stats.get("hp", max_hp))
    try:
        hp = max(0, min(max_hp, int(hp_raw)))
    except (TypeError, ValueError):
        hp = max_hp

    ac_raw = prefab_data.get("ac", base_stats.get("ac", combat.get("armor_class", 10)))
    speed_raw = prefab_data.get("speed", base_stats.get("speed", combat.get("speed", 30)))
    try:
        ac = int(ac_raw)
    except (TypeError, ValueError):
        ac = 10
    try:
        speed = int(speed_raw)
    except (TypeError, ValueError):
        speed = 30

    position = spawn_data.get("position")
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        return None
    try:
        spawn_x = int(position[0])
        spawn_y = int(position[1])
    except (TypeError, ValueError):
        return None

    entity_type = str(spawn_data.get("type") or prefab_data.get("type") or "entity").strip().lower()
    faction = (
        spawn_data.get("faction")
        or prefab_data.get("faction")
        or base_stats.get("faction")
        or "neutral"
    )

    entity: Dict[str, Any] = {
        "id": instance_id,
        "type": entity_type,
        "name": str(prefab_data.get("name") or instance_id.replace("_", " ").title()),
        "faction": str(faction),
        "ability_scores": ability_scores if isinstance(ability_scores, dict) else {},
        "speed": speed,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "status": str(prefab_data.get("status") or base_stats.get("status") or "alive"),
        "inventory": inventory,
        "equipment": equipment,
        "position": str(prefab_data.get("position") or base_stats.get("position") or "map_spawn"),
        "x": spawn_x,
        "y": spawn_y,
        "active_buffs": list(prefab_data.get("active_buffs") or base_stats.get("active_buffs") or []),
        "status_effects": list(prefab_data.get("status_effects") or base_stats.get("status_effects") or []),
        "affection": int(prefab_data.get("affection", base_stats.get("affection", 0)) or 0),
    }
    if isinstance(prefab_data.get("dynamic_states"), dict):
        entity["dynamic_states"] = copy.deepcopy(prefab_data.get("dynamic_states") or {})
    if isinstance(prefab_data.get("spell_slots"), dict):
        entity["spell_slots"] = copy.deepcopy(prefab_data.get("spell_slots") or {})
    if isinstance(prefab_data.get("spells"), (dict, list)):
        entity["spells"] = copy.deepcopy(prefab_data.get("spells"))
    if prefab_data.get("enemy_type") is not None:
        entity["enemy_type"] = prefab_data.get("enemy_type")
    return entity


def _inject_spawn_entities_into_entities(
    *,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
) -> None:
    if not isinstance(entities, dict) or not isinstance(map_data, dict):
        return
    spawns = map_data.get("spawns")
    if not isinstance(spawns, list):
        return
    for spawn in spawns:
        if not isinstance(spawn, dict):
            continue
        built = _build_spawned_entity(spawn)
        if not isinstance(built, dict):
            continue
        instance_id = str(spawn.get("instance_id") or built.get("id") or "").strip()
        if not instance_id:
            continue
        entities[instance_id] = built


def _inject_map_dynamic_entities_into_entities(
    *,
    entities: Dict[str, Any],
    map_data: Dict[str, Any],
) -> None:
    if not isinstance(entities, dict) or not isinstance(map_data, dict):
        return
    barrel_index = 1
    door_index = 1
    trap_index = 1
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        obstacle_type = str(obstacle.get("type", "")).strip().lower()
        for raw_coord in obstacle.get("coordinates", []) or []:
            if not isinstance(raw_coord, (list, tuple)) or len(raw_coord) != 2:
                continue
            x = int(raw_coord[0])
            y = int(raw_coord[1])
            if obstacle_type == "powder_barrel":
                barrel_hp = int(obstacle.get("hp", 10) or 10)
                entity_id = f"powder_barrel_{barrel_index}"
                barrel_index += 1
                entities.setdefault(
                    entity_id,
                    {
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
                    },
                )
            elif obstacle_type == "door":
                is_open = bool(obstacle.get("is_open", False))
                is_locked = bool(obstacle.get("is_locked", False))
                raw_status = str(obstacle.get("status") or "").strip().lower()
                status = raw_status or ("open" if is_open else ("locked" if is_locked else "closed"))
                entity_id = str(obstacle.get("entity_id") or f"door_{door_index}").strip().lower() or f"door_{door_index}"
                door_index += 1
                entities.setdefault(
                    entity_id,
                    {
                        "name": str(obstacle.get("name") or "沉重的橡木门"),
                        "entity_type": "door",
                        "faction": "neutral",
                        "hp": 10,
                        "max_hp": 10,
                        "ac": 10,
                        "status": status,
                        "is_open": is_open,
                        "is_locked": is_locked,
                        "key_required": obstacle.get("key_required"),
                        "lockpick_dc": int(obstacle.get("lockpick_dc", 0) or 0) or None,
                        "alias_ids": copy.deepcopy(obstacle.get("alias_ids") or []),
                        "inventory": {},
                        "equipment": {"main_hand": None, "ranged": None, "armor": None},
                        "position": "camp_center",
                        "x": x,
                        "y": y,
                        "active_buffs": [],
                        "status_effects": [],
                        "affection": 0,
                    },
                )
            elif obstacle_type == "trap":
                entity_id = (
                    str(obstacle.get("entity_id") or f"trap_{trap_index}").strip().lower()
                    or f"trap_{trap_index}"
                )
                trap_index += 1
                entities.setdefault(
                    entity_id,
                    {
                        "name": str(obstacle.get("name") or "绊线陷阱"),
                        "entity_type": "trap",
                        "faction": "neutral",
                        "hp": 1,
                        "max_hp": 1,
                        "ac": 10,
                        "status": "armed",
                        "is_hidden": bool(obstacle.get("is_hidden", True)),
                        "detect_dc": int(obstacle.get("detect_dc", 13) or 13),
                        "disarm_dc": int(obstacle.get("disarm_dc", 15) or 15),
                        "save_dc": int(obstacle.get("save_dc", 13) or 13),
                        "damage": str(obstacle.get("damage") or "2d6"),
                        "damage_type": str(obstacle.get("damage_type") or "poison").strip().lower(),
                        "trigger_radius": max(0, int(obstacle.get("trigger_radius", 0) or 0)),
                        "inventory": {},
                        "equipment": {"main_hand": None, "ranged": None, "armor": None},
                        "position": "camp_center",
                        "x": x,
                        "y": y,
                        "active_buffs": [],
                        "status_effects": [],
                        "affection": 0,
                    },
                )


def _build_environment_objects_from_map(map_data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(map_data.get("environment_objects"), dict) and map_data.get("environment_objects"):
        return copy.deepcopy(map_data.get("environment_objects") or {})
    return {
        "camp_center": {
            "name": "营地中央",
            "status": "open",
            "description": "开阔的聚落中心，可作为语义地标 (Semantic Waypoint)。",
            "x": 4,
            "y": 5,
        },
        "camp_fire": {
            "name": "篝火",
            "status": "burning",
            "description": "燃烧着的篝火，靠近可取暖。",
            "x": 4,
            "y": 6,
        },
        "iron_chest": {
            "name": "沉重的铁箱子",
            "status": "locked",
            "description": "一个上了锁的铁箱子，看起来很结实。(DC 15)",
            "inventory": {
                "gold_coin": 50,
                "rusty_dagger": 1,
                "burnt_map": 1,
            },
            "x": 6,
            "y": 2,
        },
    }


def _apply_player_start_from_map(*, entities: Dict[str, Any], map_data: Dict[str, Any]) -> None:
    if not isinstance(entities, dict) or not isinstance(map_data, dict):
        return
    player = entities.get("player")
    if not isinstance(player, dict):
        return
    raw_player_start = map_data.get("player_start")
    if not isinstance(raw_player_start, (list, tuple)) or len(raw_player_start) != 2:
        return
    try:
        px = int(raw_player_start[0])
        py = int(raw_player_start[1])
    except (TypeError, ValueError):
        return
    player["x"] = px
    player["y"] = py
    player["position"] = "map_spawn"


def get_initial_world_state(map_id: str = "training_range") -> Dict[str, Any]:
    """
    生成一个全新的、初始化的游戏世界状态（空存档创世）。
    """
    print("🌱 检测到空存档，正在生成初始世界状态...")

    # 尝试加载玩家本地背包
    init_player_inv: Dict[str, Any] = {"healing_potion": 2}
    if os.path.exists("data/player.json"):
        try:
            with open("data/player.json", "r", encoding="utf-8") as f:
                p_data = json.load(f)
                inv = p_data.get("inventory", init_player_inv)
                init_player_inv = dict(inv) if isinstance(inv, dict) else init_player_inv
        except Exception as e:
            print(f"⚠️ 无法读取 player.json，使用默认背包: {e}")

    # 构建并返回完整的初始状态字典
    map_data = get_map_data(map_id)
    if not isinstance(map_data, dict) or not map_data:
        map_data = get_map_data("training_range")
    has_spawn_table = isinstance(map_data.get("spawns"), list) and len(map_data.get("spawns", [])) > 0

    entities = _build_initial_entities(use_spawn_table=has_spawn_table)
    if has_spawn_table:
        _inject_spawn_entities_into_entities(entities=entities, map_data=map_data)
    _inject_map_dynamic_entities_into_entities(entities=entities, map_data=map_data)
    _apply_player_start_from_map(entities=entities, map_data=map_data)
    environment_objects = _build_environment_objects_from_map(map_data)
    return {
        "entities": entities,
        "map_data": map_data,
        "player_inventory": init_player_inv,
        "turn_count": 0,
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "turn_resources": {},
        "recent_barks": [],
        "active_dialogue_target": None,
        "demo_cleared": False,
        "time_of_day": "晨曦 (Morning)",
        "flags": {},
        "messages": [],
        "journal_events": [],
        "current_location": str(map_data.get("name") or "幽暗地域营地 (Underdark Camp)"),
        "environment_objects": environment_objects,
    }
