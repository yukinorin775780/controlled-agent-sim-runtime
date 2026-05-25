from core.graph.graph_routers import route_after_actor_invocation, route_after_dm


def test_route_after_dm_routes_read_to_lore_processing():
    route = route_after_dm({"intent": "READ", "is_probing_secret": False})
    assert route == "lore_processing"


def test_route_after_dm_routes_interact_with_readable_to_lore_processing():
    route = route_after_dm(
        {
            "intent": "INTERACT",
            "is_probing_secret": False,
            "intent_context": {"action_target": "hazard_diary"},
            "environment_objects": {
                "hazard_diary": {"id": "hazard_diary", "type": "readable"}
            },
        }
    )
    assert route == "lore_processing"


def test_route_after_actor_invocation_routes_runtime_to_event_drain():
    route = route_after_actor_invocation({"actor_invocation_mode": "runtime"})
    assert route == "event_drain"


def test_route_after_actor_invocation_routes_fallback_to_generation():
    route = route_after_actor_invocation({"actor_invocation_mode": "fallback"})
    assert route == "generation"


def test_route_after_actor_invocation_routes_legacy_to_generation():
    route = route_after_actor_invocation({"actor_invocation_mode": "legacy"})
    assert route == "generation"


def test_route_after_actor_invocation_defaults_missing_mode_to_generation():
    route = route_after_actor_invocation({})
    assert route == "generation"
