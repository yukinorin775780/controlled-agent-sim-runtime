from dataclasses import asdict

from core.actors.builders import build_actor_view
from core.actors.visibility import (
    build_recent_public_events,
    build_visible_history,
    filter_environment_objects_for_actor,
    filter_flags_for_actor,
)
from core.actors.views import VisibleMessage


def test_filter_flags_for_actor_only_keeps_public_prefixes():
    flags = {
        "world_lab_unlocked": True,
        "quest_found_key": False,
        "combat_active": True,
        "public_hint_seen": True,
        "director_only": True,
        "analyst_private_doubt": True,
    }

    visible = filter_flags_for_actor(flags, "analyst")

    assert visible == {
        "world_lab_unlocked": True,
        "quest_found_key": False,
        "combat_active": True,
        "public_hint_seen": True,
    }


def test_filter_flags_for_actor_supports_actor_scoped_policy():
    flags = {
        "analyst_artifact_secret": {
            "value": True,
            "visibility": {
                "scope": "actor",
                "actors": ["analyst"],
                "reason": "personal_secret",
            },
        }
    }

    analyst_visible = filter_flags_for_actor(flags, "analyst")
    scout_visible = filter_flags_for_actor(flags, "scout")

    assert analyst_visible == {"analyst_artifact_secret": True}
    assert scout_visible == {}


def test_filter_flags_for_actor_supports_party_policy_for_party_members():
    flags = {
        "party_mercy_path_known": {
            "value": True,
            "visibility": {"scope": "party"},
        }
    }
    state = {
        "entities": {
            "analyst": {"faction": "party", "status": "alive"},
            "scout": {"faction": "party", "status": "alive"},
            "drone_1": {"faction": "hostile", "status": "alive"},
        }
    }

    analyst_visible = filter_flags_for_actor(flags, "analyst", state=state)
    scout_visible = filter_flags_for_actor(flags, "scout", state=state)
    drone_visible = filter_flags_for_actor(flags, "drone_1", state=state)

    assert analyst_visible == {"party_mercy_path_known": True}
    assert scout_visible == {"party_mercy_path_known": True}
    assert drone_visible == {}


def test_filter_flags_for_actor_hidden_scope_requires_reveal_condition():
    flags = {
        "artifact_secret_revealed_note": {
            "value": True,
            "visibility": {
                "scope": "hidden",
                "reveal_when": {"flag": "world_artifact_revealed", "equals": True},
            },
        },
        "world_artifact_revealed": False,
    }

    hidden_before_reveal = filter_flags_for_actor(flags, "analyst")
    assert hidden_before_reveal == {"world_artifact_revealed": False}

    flags["world_artifact_revealed"] = True
    visible_after_reveal = filter_flags_for_actor(flags, "analyst")
    assert visible_after_reveal == {
        "artifact_secret_revealed_note": True,
        "world_artifact_revealed": True,
    }


def test_filter_flags_for_actor_does_not_leak_policy_metadata():
    flags = {
        "analyst_artifact_secret": {
            "value": True,
            "visibility": {
                "scope": "actor",
                "actors": ["analyst"],
                "reason": "personal_secret",
            },
            "hidden_metadata": {"origin": "quest_db"},
        }
    }

    visible = filter_flags_for_actor(flags, "analyst")

    assert visible == {"analyst_artifact_secret": True}
    assert isinstance(visible["analyst_artifact_secret"], bool)


def test_filter_environment_objects_for_actor_supports_visibility_policy():
    state = {
        "entities": {
            "analyst": {"faction": "party", "status": "alive"},
            "scout": {"faction": "party", "status": "alive"},
        },
        "flags": {"world_hidden_door_revealed": False},
        "environment_objects": {
            "artifact_altar": {
                "name": "Artifact Altar",
                "status": "idle",
                "visibility": {"scope": "actor", "actors": ["analyst"]},
            },
            "party_map_marker": {
                "name": "Party Marker",
                "status": "active",
                "visibility": {"scope": "party"},
            },
            "hidden_door": {
                "name": "Hidden Door",
                "status": "sealed",
                "visibility": {
                    "scope": "hidden",
                    "reveal_when": {"flag": "world_hidden_door_revealed", "equals": True},
                },
            },
        },
    }

    analyst_visible = filter_environment_objects_for_actor(state, "analyst")
    scout_visible = filter_environment_objects_for_actor(state, "scout")

    assert "artifact_altar" in analyst_visible
    assert "artifact_altar" not in scout_visible
    assert "party_map_marker" in analyst_visible
    assert "party_map_marker" in scout_visible
    assert "hidden_door" not in analyst_visible

    state["flags"]["world_hidden_door_revealed"] = True
    analyst_visible_after_reveal = filter_environment_objects_for_actor(state, "analyst")
    assert "hidden_door" in analyst_visible_after_reveal
    assert "visibility" not in analyst_visible_after_reveal["artifact_altar"]


def test_build_visible_history_normalizes_to_visible_message():
    messages = [
        {"role": "user", "content": "你是谁？"},
        {"role": "assistant", "name": "gatekeeper", "content": "[gatekeeper]: 离远点。"},
    ]

    visible_history = build_visible_history(messages, actor_id="gatekeeper", limit=12)

    assert len(visible_history) == 2
    assert all(isinstance(item, VisibleMessage) for item in visible_history)
    assert visible_history[1].speaker_id == "gatekeeper"
    assert visible_history[1].content == "离远点。"


def test_build_recent_public_events_returns_tail_slice():
    events = [f"event-{idx}" for idx in range(1, 11)]
    assert build_recent_public_events(events, limit=4) == [
        "event-7",
        "event-8",
        "event-9",
        "event-10",
    ]


def test_actor_view_never_contains_speaker_queue():
    state = {
        "speaker_queue": ["scout", "analyst"],
        "messages": [],
        "entities": {
            "analyst": {
                "name": "Analyst",
                "hp": 10,
                "max_hp": 10,
                "inventory": {"healing_potion": 1},
                "affection": 10,
                "active_buffs": [],
                "position": "camp_center",
                "status": "alive",
                "faction": "party",
            }
        },
    }

    actor_view = build_actor_view(state, "analyst")
    serialized = asdict(actor_view)

    assert "speaker_queue" not in serialized
