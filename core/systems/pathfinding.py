"""
Grid pathfinding utilities.
"""

from __future__ import annotations

import heapq
from typing import Any, Dict, Iterable, List, Optional, Tuple


GridPos = Tuple[int, int]

_NEIGHBOR_DELTAS: Tuple[GridPos, ...] = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


def _to_pos(raw: Any) -> Optional[GridPos]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None


def _chebyshev(a: GridPos, b: GridPos) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


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


def _collect_blocked_tiles(map_data: Dict[str, Any]) -> set[GridPos]:
    blocked: set[GridPos] = set()
    for raw_tile in map_data.get("blocked_movement_tiles", []) or []:
        pos = _to_pos(raw_tile)
        if pos is not None:
            blocked.add(pos)

    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        if not _obstacle_blocks_movement(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            pos = _to_pos(raw_coord)
            if pos is not None:
                blocked.add(pos)
    return blocked


def _collect_los_blocked_tiles(map_data: Dict[str, Any]) -> set[GridPos]:
    blocked: set[GridPos] = set()
    for obstacle in map_data.get("obstacles", []) or []:
        if not isinstance(obstacle, dict):
            continue
        if not _obstacle_blocks_los(obstacle):
            continue
        for raw_coord in obstacle.get("coordinates", []) or []:
            pos = _to_pos(raw_coord)
            if pos is not None:
                blocked.add(pos)
    return blocked


def _build_bounds(start: GridPos, goal: GridPos, map_data: Dict[str, Any]) -> Tuple[int, int, int, int]:
    width = int(map_data.get("width", 0) or 0)
    height = int(map_data.get("height", 0) or 0)
    if width > 0 and height > 0:
        return 0, width - 1, 0, height - 1

    margin = 20
    min_x = min(start[0], goal[0]) - margin
    max_x = max(start[0], goal[0]) + margin
    min_y = min(start[1], goal[1]) - margin
    max_y = max(start[1], goal[1]) + margin
    return min_x, max_x, min_y, max_y


def _is_inside_bounds(pos: GridPos, bounds: Tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = bounds
    return min_x <= pos[0] <= max_x and min_y <= pos[1] <= max_y


def _goal_candidates(
    *,
    start: GridPos,
    goal: GridPos,
    bounds: Tuple[int, int, int, int],
    blocked_tiles: set[GridPos],
    occupied_tiles: set[GridPos],
) -> List[GridPos]:
    candidates: List[GridPos] = []

    def _is_passable(pos: GridPos) -> bool:
        if not _is_inside_bounds(pos, bounds):
            return False
        if pos in blocked_tiles:
            return False
        if pos in occupied_tiles:
            return False
        return True

    if _is_passable(goal):
        candidates.append(goal)

    for dx, dy in _NEIGHBOR_DELTAS:
        neighbor = (goal[0] + dx, goal[1] + dy)
        if _is_passable(neighbor):
            candidates.append(neighbor)

    candidates = list(dict.fromkeys(candidates))
    candidates.sort(
        key=lambda pos: (
            _chebyshev(start, pos),
            abs(pos[0] - goal[0]) + abs(pos[1] - goal[1]),
            abs(pos[0] - start[0]),
            abs(pos[1] - start[1]),
            pos[1],
            pos[0],
        )
    )
    return candidates


def _reconstruct_path(came_from: Dict[GridPos, GridPos], node: GridPos) -> List[GridPos]:
    path = [node]
    while node in came_from:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


def bresenham_line(start: GridPos, end: GridPos) -> List[GridPos]:
    """
    Return all grid cells crossed by a line segment from start to end (inclusive).
    """
    x0, y0 = int(start[0]), int(start[1])
    x1, y1 = int(end[0]), int(end[1])

    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    points: List[GridPos] = []
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return points


def check_line_of_sight(start_coords: GridPos, target_coords: GridPos, map_data: Dict[str, Any]) -> bool:
    """
    Check if LOS from start to target is blocked by obstacles with blocks_los=true.
    Start/end tiles themselves are ignored for obstruction checks.
    """
    blocked_tiles = _collect_los_blocked_tiles(map_data if isinstance(map_data, dict) else {})
    if not blocked_tiles:
        return True

    line_cells = bresenham_line(start_coords, target_coords)
    if len(line_cells) <= 2:
        return True

    for cell in line_cells[1:-1]:
        if cell in blocked_tiles:
            return False
    return True


def _a_star_single_goal(
    *,
    start: GridPos,
    goal: GridPos,
    bounds: Tuple[int, int, int, int],
    blocked_tiles: set[GridPos],
    occupied_tiles: set[GridPos],
) -> List[GridPos]:
    open_heap: List[Tuple[int, int, GridPos]] = []
    came_from: Dict[GridPos, GridPos] = {}
    g_score: Dict[GridPos, int] = {start: 0}
    tie = 0
    heapq.heappush(open_heap, (_chebyshev(start, goal), tie, start))

    explored = 0
    max_explored = 10000

    while open_heap and explored < max_explored:
        _, _, current = heapq.heappop(open_heap)
        explored += 1
        if current == goal:
            return _reconstruct_path(came_from, current)

        current_g = g_score.get(current, 10**9)
        for dx, dy in _NEIGHBOR_DELTAS:
            neighbor = (current[0] + dx, current[1] + dy)
            if not _is_inside_bounds(neighbor, bounds):
                continue
            if neighbor in blocked_tiles:
                continue
            if neighbor in occupied_tiles and neighbor != goal:
                continue

            tentative_g = current_g + 1
            if tentative_g >= g_score.get(neighbor, 10**9):
                continue

            came_from[neighbor] = current
            g_score[neighbor] = tentative_g
            heuristic = _chebyshev(neighbor, goal)
            tie += 1
            heapq.heappush(open_heap, (tentative_g + heuristic, tie, neighbor))

    return []


def a_star_path(
    start: GridPos,
    goal: GridPos,
    map_data: Dict[str, Any],
    entities_positions: Iterable[GridPos],
) -> List[GridPos]:
    """
    Compute path from start to goal (or a legal adjacent tile if goal is blocked/occupied).
    Returns path as [start, ..., destination]. Returns [] when unreachable.
    """
    start = (int(start[0]), int(start[1]))
    goal = (int(goal[0]), int(goal[1]))
    if start == goal:
        return [start]

    bounds = _build_bounds(start, goal, map_data if isinstance(map_data, dict) else {})
    blocked_tiles = _collect_blocked_tiles(map_data if isinstance(map_data, dict) else {})
    occupied_tiles: set[GridPos] = set()
    for raw in entities_positions or []:
        pos = _to_pos(raw)
        if pos is not None:
            occupied_tiles.add(pos)
    occupied_tiles.discard(start)

    goals = _goal_candidates(
        start=start,
        goal=goal,
        bounds=bounds,
        blocked_tiles=blocked_tiles,
        occupied_tiles=occupied_tiles,
    )
    for candidate in goals:
        path = _a_star_single_goal(
            start=start,
            goal=candidate,
            bounds=bounds,
            blocked_tiles=blocked_tiles,
            occupied_tiles=occupied_tiles,
        )
        if path:
            return path
    return []
