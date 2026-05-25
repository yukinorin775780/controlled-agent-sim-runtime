from core.graph.nodes.input import input_node


def test_flag_command_keeps_legacy_boolean_behavior():
    state = {
        "user_input": "/flag world_lab_unlocked true",
        "flags": {"world_existing": False},
        "entities": {},
    }

    patch = input_node(state)

    assert patch["intent"] == "command_done"
    assert patch["flags"]["world_existing"] is False
    assert patch["flags"]["world_lab_unlocked"] is True


def test_flag_command_supports_json_policy_payload():
    state = {
        "user_input": (
            '/flag analyst_artifact_secret '
            '{"value":true,"visibility":{"scope":"actor","actors":["analyst"]}}'
        ),
        "flags": {},
        "entities": {},
    }

    patch = input_node(state)

    assert patch["intent"] == "command_done"
    assert patch["flags"]["analyst_artifact_secret"] == {
        "value": True,
        "visibility": {"scope": "actor", "actors": ["analyst"]},
    }
