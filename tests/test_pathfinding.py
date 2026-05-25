from core.systems.pathfinding import a_star_path, check_line_of_sight
from core.systems.world_init import get_initial_world_state


def test_a_star_path_routes_to_adjacent_tile_when_goal_is_occupied():
    state = get_initial_world_state()
    map_data = state["map_data"]
    start = (5, 8)
    goal = (5, 4)

    path = a_star_path(
        start=start,
        goal=goal,
        map_data=map_data,
        entities_positions=[goal],
    )

    blocked_tiles = {tuple(tile) for tile in map_data.get("blocked_movement_tiles", [])}
    assert path
    assert path[0] == start
    assert max(abs(path[-1][0] - goal[0]), abs(path[-1][1] - goal[1])) <= 1
    assert all(step not in blocked_tiles for step in path[1:])


def test_a_star_path_returns_empty_when_no_reachable_goal_or_adjacent_tile():
    map_data = {
        "width": 3,
        "height": 3,
        "obstacles": [
            {
                "type": "wall",
                "coordinates": [[1, 1], [1, 2], [2, 1]],
                "blocks_movement": True,
                "blocks_los": True,
            }
        ],
    }

    path = a_star_path(
        start=(0, 0),
        goal=(2, 2),
        map_data=map_data,
        entities_positions=[(2, 2)],
    )

    assert path == []


def test_check_line_of_sight_blocked_by_rock_obstacle():
    state = get_initial_world_state()
    map_data = state["map_data"]

    assert check_line_of_sight((5, 8), (5, 4), map_data) is False


def test_check_line_of_sight_clear_when_no_blocking_obstacle():
    state = get_initial_world_state()
    map_data = state["map_data"]

    assert check_line_of_sight((4, 9), (4, 3), map_data) is True
