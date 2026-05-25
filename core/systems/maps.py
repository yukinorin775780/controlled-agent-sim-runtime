"""
Static tactical map loader (YAML-driven).
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml


MAP_DB: Dict[str, Dict[str, Any]] = {}
_LOADED = False


def _default_maps_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "maps.yaml")
    )


def _default_maps_dir() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "maps")
    )


def _normalize_map_id(map_id: Any) -> str:
    return str(map_id or "").strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_coord(coord: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(coord, (list, tuple)) or len(coord) != 2:
        return None
    try:
        return int(coord[0]), int(coord[1])
    except (TypeError, ValueError):
        return None


def _normalize_grid_rows(raw_grid: Any) -> List[List[str]]:
    normalized_rows: List[List[str]] = []
    if not isinstance(raw_grid, list):
        return normalized_rows

    for raw_row in raw_grid:
        cells: List[str] = []
        if isinstance(raw_row, str):
            stripped = raw_row.strip()
            if not stripped:
                continue
            parts = [part.strip().upper() for part in stripped.split() if part.strip()]
            if parts:
                cells = parts
            else:
                cells = [char.upper() for char in stripped if not char.isspace()]
        elif isinstance(raw_row, list):
            cells = [str(cell or "").strip().upper() for cell in raw_row if str(cell or "").strip()]
        if not cells:
            continue
        normalized_rows.append(cells)

    if not normalized_rows:
        return []

    expected_width = len(normalized_rows[0])
    if expected_width <= 0:
        return []

    aligned_rows: List[List[str]] = []
    for row in normalized_rows:
        if len(row) < expected_width:
            row = row + ["."] * (expected_width - len(row))
        elif len(row) > expected_width:
            row = row[:expected_width]
        aligned_rows.append(row)
    return aligned_rows


def _build_wall_coords_from_grid(grid_rows: List[List[str]]) -> List[List[int]]:
    wall_coords: List[List[int]] = []
    for y, row in enumerate(grid_rows):
        for x, marker in enumerate(row):
            if marker == "W":
                wall_coords.append([x, y])
    return wall_coords


def _build_blocked_tiles(obstacles: List[Dict[str, Any]]) -> List[List[int]]:
    blocked: set[Tuple[int, int]] = set()
    for obstacle in obstacles:
        if not isinstance(obstacle, dict):
            continue
        if not _obstacle_blocks_movement(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            coord = _normalize_coord(raw_coord)
            if coord is None:
                continue
            blocked.add(coord)
    return [[x, y] for x, y in sorted(blocked)]


def _obstacle_blocks_movement(obstacle: Dict[str, Any]) -> bool:
    obstacle_type = str(obstacle.get("type", "")).strip().lower()
    if obstacle_type == "door":
        return not bool(obstacle.get("is_open", False))
    return bool(obstacle.get("blocks_movement", False))


def _normalize_obstacle(raw_obstacle: Dict[str, Any]) -> Dict[str, Any]:
    normalized_obstacle: Dict[str, Any] = {
        "type": str(raw_obstacle.get("type", "obstacle")).strip().lower() or "obstacle",
        "coordinates": [],
        "blocks_movement": bool(raw_obstacle.get("blocks_movement", False)),
        "blocks_los": bool(raw_obstacle.get("blocks_los", False)),
    }
    if "name" in raw_obstacle:
        normalized_obstacle["name"] = str(raw_obstacle.get("name") or "").strip()
    if "entity_id" in raw_obstacle:
        normalized_obstacle["entity_id"] = str(raw_obstacle.get("entity_id") or "").strip().lower()
    if "is_open" in raw_obstacle:
        normalized_obstacle["is_open"] = bool(raw_obstacle.get("is_open"))
    if "is_locked" in raw_obstacle:
        normalized_obstacle["is_locked"] = bool(raw_obstacle.get("is_locked"))
    if "status" in raw_obstacle:
        normalized_obstacle["status"] = str(raw_obstacle.get("status") or "").strip().lower()
    if "key_required" in raw_obstacle:
        normalized_obstacle["key_required"] = str(raw_obstacle.get("key_required") or "").strip()
    if "lockpick_dc" in raw_obstacle:
        try:
            normalized_obstacle["lockpick_dc"] = int(raw_obstacle.get("lockpick_dc"))
        except (TypeError, ValueError):
            normalized_obstacle["lockpick_dc"] = 15
    if "alias_ids" in raw_obstacle:
        raw_aliases = raw_obstacle.get("alias_ids")
        normalized_obstacle["alias_ids"] = list(raw_aliases) if isinstance(raw_aliases, list) else []
    if "target_map" in raw_obstacle:
        normalized_obstacle["target_map"] = _normalize_map_id(raw_obstacle.get("target_map"))
    if "spawn_x" in raw_obstacle:
        try:
            normalized_obstacle["spawn_x"] = int(raw_obstacle.get("spawn_x"))
        except (TypeError, ValueError):
            normalized_obstacle["spawn_x"] = 0
    if "spawn_y" in raw_obstacle:
        try:
            normalized_obstacle["spawn_y"] = int(raw_obstacle.get("spawn_y"))
        except (TypeError, ValueError):
            normalized_obstacle["spawn_y"] = 0
    if "hp" in raw_obstacle:
        try:
            normalized_obstacle["hp"] = max(1, int(raw_obstacle.get("hp")))
        except (TypeError, ValueError):
            normalized_obstacle["hp"] = 10
    if "is_hidden" in raw_obstacle:
        normalized_obstacle["is_hidden"] = bool(raw_obstacle.get("is_hidden"))
    if "detect_dc" in raw_obstacle:
        try:
            normalized_obstacle["detect_dc"] = int(raw_obstacle.get("detect_dc"))
        except (TypeError, ValueError):
            normalized_obstacle["detect_dc"] = 13
    if "disarm_dc" in raw_obstacle:
        try:
            normalized_obstacle["disarm_dc"] = int(raw_obstacle.get("disarm_dc"))
        except (TypeError, ValueError):
            normalized_obstacle["disarm_dc"] = 15
    if "save_dc" in raw_obstacle:
        try:
            normalized_obstacle["save_dc"] = int(raw_obstacle.get("save_dc"))
        except (TypeError, ValueError):
            normalized_obstacle["save_dc"] = 13
    if "trigger_radius" in raw_obstacle:
        try:
            normalized_obstacle["trigger_radius"] = max(0, int(raw_obstacle.get("trigger_radius")))
        except (TypeError, ValueError):
            normalized_obstacle["trigger_radius"] = 0
    if "damage" in raw_obstacle:
        normalized_obstacle["damage"] = str(raw_obstacle.get("damage") or "2d6")
    if "damage_type" in raw_obstacle:
        normalized_obstacle["damage_type"] = str(raw_obstacle.get("damage_type") or "").strip().lower()
    for raw_coord in raw_obstacle.get("coordinates", []) or []:
        coord = _normalize_coord(raw_coord)
        if coord is None:
            continue
        normalized_obstacle["coordinates"].append([coord[0], coord[1]])
    return normalized_obstacle


def _normalize_environment_objects(
    raw_environment_objects: Any,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if isinstance(raw_environment_objects, dict):
        return copy.deepcopy(raw_environment_objects), []

    normalized_objects: Dict[str, Any] = {}
    derived_obstacles: List[Dict[str, Any]] = []
    if not isinstance(raw_environment_objects, list):
        return normalized_objects, derived_obstacles

    for idx, raw_obj in enumerate(raw_environment_objects, start=1):
        if not isinstance(raw_obj, dict):
            continue
        object_id = str(raw_obj.get("id") or f"env_{idx}").strip()
        if not object_id:
            continue
        normalized_obj = copy.deepcopy(raw_obj)
        raw_position = raw_obj.get("position")
        coord = _normalize_coord(raw_position)
        if coord is not None:
            normalized_obj["x"] = coord[0]
            normalized_obj["y"] = coord[1]
            normalized_obj.pop("position", None)
        normalized_objects[object_id] = normalized_obj

        object_type = str(raw_obj.get("type", "")).strip().lower()
        if object_type not in {"door", "trap", "transition_zone", "powder_barrel"}:
            continue
        if coord is None:
            continue
        derived_obstacle: Dict[str, Any] = {
            "type": object_type,
            "entity_id": object_id.lower(),
            "name": str(raw_obj.get("name") or object_id),
            "coordinates": [[coord[0], coord[1]]],
            "blocks_movement": bool(raw_obj.get("blocks_movement", False)),
            "blocks_los": bool(raw_obj.get("blocks_los", False)),
        }
        if object_type == "door":
            is_open = bool(raw_obj.get("is_open", False))
            derived_obstacle["is_open"] = is_open
            derived_obstacle["is_locked"] = bool(raw_obj.get("is_locked", False))
            derived_obstacle["status"] = str(raw_obj.get("status") or ("open" if is_open else "closed"))
            if raw_obj.get("key_required"):
                derived_obstacle["key_required"] = str(raw_obj.get("key_required") or "")
            if raw_obj.get("lockpick_dc") is not None:
                derived_obstacle["lockpick_dc"] = int(raw_obj.get("lockpick_dc") or 0)
            if raw_obj.get("alias_ids") is not None:
                derived_obstacle["alias_ids"] = copy.deepcopy(raw_obj.get("alias_ids") or [])
            derived_obstacle["blocks_movement"] = not is_open
            derived_obstacle["blocks_los"] = not is_open
        if object_type == "trap":
            derived_obstacle["is_hidden"] = bool(raw_obj.get("is_hidden", True))
            derived_obstacle["detect_dc"] = int(raw_obj.get("detect_dc", 13) or 13)
            derived_obstacle["disarm_dc"] = int(raw_obj.get("disarm_dc", 15) or 15)
            derived_obstacle["damage"] = str(raw_obj.get("damage") or "2d6")
            derived_obstacle["save_dc"] = int(raw_obj.get("save_dc", 13) or 13)
            derived_obstacle["trigger_radius"] = max(0, int(raw_obj.get("trigger_radius", 0) or 0))
        if object_type == "transition_zone":
            derived_obstacle["target_map"] = _normalize_map_id(raw_obj.get("target_map"))
            derived_obstacle["spawn_x"] = int(raw_obj.get("spawn_x", 0) or 0)
            derived_obstacle["spawn_y"] = int(raw_obj.get("spawn_y", 0) or 0)
        if object_type == "powder_barrel":
            derived_obstacle["hp"] = max(1, int(raw_obj.get("hp", 10) or 10))
            derived_obstacle["blocks_movement"] = True
            derived_obstacle["blocks_los"] = True
        derived_obstacles.append(derived_obstacle)
    return normalized_objects, derived_obstacles


def _normalize_map_entry(raw_map_id: str, raw_map_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    map_id = _normalize_map_id(raw_map_data.get("map_id") or raw_map_id)
    if not map_id:
        return None

    grid_rows = _normalize_grid_rows(raw_map_data.get("grid"))
    dimensions = raw_map_data.get("dimensions")
    width: int
    height: int
    if grid_rows:
        width = len(grid_rows[0])
        height = len(grid_rows)
    elif isinstance(dimensions, (list, tuple)) and len(dimensions) == 2:
        try:
            width = int(dimensions[0])
            height = int(dimensions[1])
        except (TypeError, ValueError):
            width = int(raw_map_data.get("width", 15))
            height = int(raw_map_data.get("height", 15))
    else:
        width = int(raw_map_data.get("width", 15))
        height = int(raw_map_data.get("height", 15))

    obstacles: List[Dict[str, Any]] = []
    for raw_obstacle in raw_map_data.get("obstacles", []) or []:
        if not isinstance(raw_obstacle, dict):
            continue
        obstacles.append(_normalize_obstacle(raw_obstacle))

    normalized_environment_objects, derived_obstacles = _normalize_environment_objects(
        raw_map_data.get("environment_objects")
    )
    if derived_obstacles:
        obstacles.extend(_normalize_obstacle(obstacle) for obstacle in derived_obstacles)

    if grid_rows:
        wall_coords = _build_wall_coords_from_grid(grid_rows)
        carve_out: set[Tuple[int, int]] = set()
        for obstacle in obstacles:
            if not isinstance(obstacle, dict):
                continue
            obstacle_type = str(obstacle.get("type", "")).strip().lower()
            if obstacle_type not in {"door", "transition_zone"}:
                continue
            for raw_coord in obstacle.get("coordinates", []) or []:
                coord = _normalize_coord(raw_coord)
                if coord is not None:
                    carve_out.add(coord)

        filtered_wall_coords = [
            [x, y]
            for x, y in wall_coords
            if (x, y) not in carve_out
        ]
        if filtered_wall_coords:
            obstacles.insert(
                0,
                _normalize_obstacle(
                    {
                        "type": "wall",
                        "coordinates": filtered_wall_coords,
                        "blocks_movement": True,
                        "blocks_los": True,
                    }
                ),
            )

    player_start = _normalize_coord(raw_map_data.get("player_start"))

    return {
        "id": map_id,
        "name": str(raw_map_data.get("name") or raw_map_id),
        "width": max(1, width),
        "height": max(1, height),
        "obstacles": obstacles,
        "blocked_movement_tiles": _build_blocked_tiles(obstacles),
        "environment_objects": normalized_environment_objects,
        "spawns": copy.deepcopy(raw_map_data.get("spawns") or []),
        "player_start": [player_start[0], player_start[1]] if player_start is not None else None,
    }


def _ingest_map_file(filepath: str) -> None:
    if not os.path.exists(filepath):
        return
    with open(filepath, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    if not isinstance(raw_data, dict):
        return

    if "map_id" in raw_data:
        normalized_map = _normalize_map_entry(str(raw_data.get("map_id") or ""), raw_data)
        if normalized_map is not None:
            MAP_DB[normalized_map["id"]] = normalized_map
        return

    for raw_map_id, raw_map_data in raw_data.items():
        if not isinstance(raw_map_data, dict):
            continue
        normalized_map = _normalize_map_entry(str(raw_map_id), raw_map_data)
        if normalized_map is None:
            continue
        MAP_DB[normalized_map["id"]] = normalized_map


def load_maps(filepath: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    global _LOADED
    if _LOADED and not force_reload:
        return MAP_DB

    target_path = filepath or _default_maps_path()
    MAP_DB.clear()
    _ingest_map_file(target_path)

    maps_dir = _default_maps_dir()
    if os.path.isdir(maps_dir):
        for filename in sorted(os.listdir(maps_dir)):
            if not filename.endswith((".yaml", ".yml")):
                continue
            _ingest_map_file(os.path.join(maps_dir, filename))

    _LOADED = True
    return MAP_DB


def get_map_data(map_id: str) -> Dict[str, Any]:
    load_maps()
    resolved_id = _normalize_map_id(map_id)
    if not resolved_id:
        return {}
    return copy.deepcopy(MAP_DB.get(resolved_id, {}))
