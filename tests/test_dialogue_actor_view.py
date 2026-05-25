from unittest.mock import patch

from core.actors.views import ActorSelfState, ActorView, PublicEntityView, VisibleMessage
from core.graph.nodes.dialogue import _build_dialogue_prompt, dialogue_node


def _make_dialogue_actor_view() -> ActorView:
    return ActorView(
        actor_id="gatekeeper",
        user_input="你在守什么？",
        intent="DIALOGUE_REPLY",
        intent_context={},
        is_probing_secret=False,
        self_state=ActorSelfState(
            actor_id="gatekeeper",
            name="Gatekeeper",
            hp=18,
            max_hp=18,
            inventory={"heavy_iron_key": 1},
            affection=0,
            active_buffs=[],
            position="lab_hall",
            dynamic_states={"patience": 15, "fear": 5},
        ),
        other_entities={
            "scout": PublicEntityView(
                entity_id="scout",
                name="Scout",
                position="lab_hall",
                status="alive",
                faction="party",
            )
        },
        current_location="hazard_lab",
        time_of_day="深夜",
        turn_count=5,
        visible_environment_objects={},
        visible_flags={"world_lab_unlocked": True},
        visible_history=[
            VisibleMessage(role="user", speaker_id="user", content="开门"),
            VisibleMessage(role="assistant", speaker_id="gatekeeper", content="滚开。"),
        ],
        recent_public_events=["event"],
        latest_roll={},
        memory_snippets=["他被重金收买过一次。"],
    )


def test_dialogue_node_uses_actor_view_for_prompt():
    state = {
        "intent": "DIALOGUE_REPLY",
        "intent_context": {"action_target": "gatekeeper"},
        "active_dialogue_target": "gatekeeper",
        "user_input": "说话",
        "entities": {
            "gatekeeper": {
                "name": "Gatekeeper",
                "status": "alive",
                "hp": 18,
                "max_hp": 18,
                "inventory": {"heavy_iron_key": 1},
                "dynamic_states": {"patience": {"current_value": 15}, "fear": {"current_value": 5}},
            }
        },
        "player_inventory": {},
    }
    actor_view = _make_dialogue_actor_view()

    with patch(
        "core.graph.nodes.dialogue.build_actor_view",
        return_value=actor_view,
    ) as build_actor_view, patch(
        "core.graph.nodes.dialogue._build_dialogue_prompt",
        return_value="PROMPT",
    ) as build_prompt, patch(
        "core.graph.nodes.dialogue._run_blocking_with_timeout",
        return_value='{"internal_monologue":"","reply":"……","trigger_combat":false,"state_changes":{"patience_delta":0,"fear_delta":0}}',
    ):
        result = dialogue_node(state)

    build_actor_view.assert_called_once()
    _, kwargs = build_prompt.call_args
    assert kwargs["actor_view"] is actor_view
    assert "state" not in kwargs
    assert result["active_dialogue_target"] == "gatekeeper"


def test_build_dialogue_prompt_does_not_leak_peer_private_inventory():
    actor_view = _make_dialogue_actor_view()
    prompt = _build_dialogue_prompt(
        target_id="gatekeeper",
        target_entity={
            "name": "Gatekeeper",
            "template_path": "prompts/hostile_npc_template.j2",
            "inventory": {"heavy_iron_key": 1},
            "dynamic_states": {"patience": {"current_value": 15}, "fear": {"current_value": 5}},
        },
        actor_view=actor_view,
        user_input="你在守什么？",
    )

    assert "他被重金收买过一次。" in prompt
    assert "Scout(scout)" in prompt
    # peer private state should never appear in hostile prompt
    assert "healing_potion" not in prompt
    assert "secret_objective\": \"Hide unauthorized backchannel" not in prompt

