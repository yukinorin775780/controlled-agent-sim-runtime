import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage

import core.graph.nodes.generation as generation
from core.actors.views import ActorSelfState, ActorView, PublicEntityView, VisibleMessage


def _make_actor_view() -> ActorView:
    return ActorView(
        actor_id="analyst",
        user_input="你好",
        intent="CHAT",
        intent_context={},
        is_probing_secret=False,
        self_state=ActorSelfState(
            actor_id="analyst",
            name="Analyst",
            hp=10,
            max_hp=10,
            inventory={"healing_potion": 1},
            affection=15,
            active_buffs=[],
            position="camp_center",
            dynamic_states={},
        ),
        other_entities={
            "scout": PublicEntityView(
                entity_id="scout",
                name="Scout",
                position="camp_center",
                status="alive",
                faction="party",
            )
        },
        current_location="camp_center",
        time_of_day="黄昏",
        turn_count=3,
        visible_environment_objects={"camp_fire": {"name": "篝火", "status": "burning"}},
        visible_flags={"world_ready": True},
        visible_history=[
            VisibleMessage(role="user", speaker_id="user", content="你好"),
            VisibleMessage(role="assistant", speaker_id="scout", content="嗯哼？"),
        ],
        recent_public_events=["事件A"],
        latest_roll={},
        memory_snippets=["她记得玩家曾帮助过她。"],
    )


def _make_scout_actor_view() -> ActorView:
    return ActorView(
        actor_id="scout",
        user_input="继续",
        intent="CHAT",
        intent_context={},
        is_probing_secret=False,
        self_state=ActorSelfState(
            actor_id="scout",
            name="Scout",
            hp=12,
            max_hp=12,
            inventory={"dagger": 1},
            affection=20,
            active_buffs=[],
            position="camp_center",
            dynamic_states={},
        ),
        other_entities={
            "analyst": PublicEntityView(
                entity_id="analyst",
                name="Analyst",
                position="camp_center",
                status="alive",
                faction="party",
            )
        },
        current_location="camp_center",
        time_of_day="黄昏",
        turn_count=3,
        visible_environment_objects={"camp_fire": {"name": "篝火", "status": "burning"}},
        visible_flags={"world_ready": True},
        visible_history=[
            VisibleMessage(role="user", speaker_id="user", content="继续"),
            VisibleMessage(role="assistant", speaker_id="analyst", content="别走神。"),
        ],
        recent_public_events=["事件A"],
        latest_roll={},
        memory_snippets=["侦察员记得这场谈话。"],
    )


def _make_tactician_actor_view() -> ActorView:
    return ActorView(
        actor_id="tactician",
        user_input="继续",
        intent="CHAT",
        intent_context={},
        is_probing_secret=False,
        self_state=ActorSelfState(
            actor_id="tactician",
            name="Tactician",
            hp=13,
            max_hp=13,
            inventory={"longsword": 1},
            affection=10,
            active_buffs=[],
            position="camp_center",
            dynamic_states={},
        ),
        other_entities={
            "analyst": PublicEntityView(
                entity_id="analyst",
                name="Analyst",
                position="camp_center",
                status="alive",
                faction="party",
            ),
            "scout": PublicEntityView(
                entity_id="scout",
                name="Scout",
                position="camp_center",
                status="alive",
                faction="party",
            ),
        },
        current_location="camp_center",
        time_of_day="黄昏",
        turn_count=3,
        visible_environment_objects={"camp_fire": {"name": "篝火", "status": "burning"}},
        visible_flags={"world_ready": True},
        visible_history=[
            VisibleMessage(role="user", speaker_id="user", content="继续"),
            VisibleMessage(role="assistant", speaker_id="analyst", content="集中注意。"),
        ],
        recent_public_events=["事件A"],
        latest_roll={},
        memory_snippets=["战术员记得上次交锋。"],
    )


def test_generation_node_routes_prompt_building_through_actor_view():
    node = generation.create_generation_node()
    state = {
        "entities": {"analyst": {"hp": 10, "affection": 15, "inventory": {}}},
        "current_speaker": "analyst",
        "user_input": "你好",
    }
    fake_character = Mock()
    actor_view = _make_actor_view()
    context = {
        "speaker": "analyst",
        "idle_banter": False,
        "player_inv_for_physics": {},
        "current_entities": {"analyst": {"hp": 10, "position": "camp_center"}},
        "entities": {"analyst": {"hp": 10, "position": "camp_center"}},
        "current_env_objs": {},
        "history_dicts": [{"role": "user", "content": "你好"}],
    }

    with patch("characters.loader.load_character", return_value=fake_character), patch(
        "core.graph.nodes.generation.build_actor_view",
        return_value=actor_view,
    ) as build_actor_view, patch(
        "core.graph.nodes.generation._build_unconscious_response",
        return_value=None,
    ), patch(
        "core.graph.nodes.generation._prepare_generation_context",
        return_value=context,
    ) as prepare_context, patch(
        "core.graph.nodes.generation._maybe_generate_banter_response",
        return_value=None,
    ), patch(
        "core.graph.nodes.generation._build_system_prompt",
        return_value="SYSTEM",
    ) as build_system_prompt, patch(
        "core.graph.nodes.generation._build_lc_messages",
        return_value=[HumanMessage(content="你好")],
    ), patch(
        "core.graph.nodes.generation._create_llm_client",
        return_value=SimpleNamespace(ainvoke=AsyncMock()),
    ), patch(
        "core.graph.nodes.generation._execute_llm_with_tools",
        return_value=(AIMessage(content='{"reply":"ok"}'), []),
    ), patch(
        "core.graph.nodes.generation._parse_and_apply_actions",
        return_value={
            "clean_text": "ok",
            "thought_process": "",
            "tool_physics_events": [],
            "state_changes_applied": False,
            "idle_merged": None,
        },
    ), patch(
        "core.graph.nodes.generation._assemble_generation_output",
        return_value={"ok": True},
    ):
        result = asyncio.run(node(state))

    assert result == {"ok": True}
    build_actor_view.assert_called_once()
    _, kwargs = prepare_context.call_args
    assert kwargs["actor_view"] is actor_view
    build_system_prompt.assert_called_once_with(actor_view, context)


def test_format_history_messages_consumes_actor_view_only():
    actor_view = _make_actor_view()
    context = {
        "user_input": "继续",
        "is_first_npc_of_player_turn": False,
        "idle_banter": False,
        "intent": "chat",
        "speaker": "analyst",
        "npc_inv": {"healing_potion": 1},
        "prev_responses": [],
    }

    history_dicts = generation._format_history_messages(actor_view, context)

    assert history_dicts[0] == {"role": "user", "content": "你好"}
    assert history_dicts[1]["role"] == "assistant"
    assert "🚨 [CRITICAL OVERRIDE - PHYSICAL ACTION REQUIRED]:" not in history_dicts[0]["content"]


def test_system_prompt_item_lore_only_uses_actor_visible_items():
    actor_view = replace(
        _make_actor_view(),
        visible_environment_objects={
            "iron_chest": {
                "name": "沉重的铁箱子",
                "status": "opened",
                "description": "箱子已经被打开。",
                "inventory": {"gold_coin": 3},
            }
        },
    )
    fake_character = Mock()
    fake_character.render_prompt.return_value = "BASE_PROMPT\n"
    context = {
        "speaker": "analyst",
        "character": fake_character,
        "idle_banter": False,
        "current_npc_data": {"hp": 10, "active_buffs": []},
        "affection": 15,
        "flags": {"world_ready": True},
        "summary": "summary",
        "journal_events": [],
        "inventory_display_list": [],
        "has_healing_potion": True,
        "player_inv": {},
        "latest_roll": {},
        "prev_responses": [],
        "environment": {
            "current_location": "camp_center",
            "current_env_objs": actor_view.visible_environment_objects,
        },
        "entities": {
            "analyst": {"inventory": {"healing_potion": 1}},
            "scout": {"inventory": {"private_dagger": 1, "ruby_ring": 1}},
        },
    }

    prompt = generation._build_system_prompt(actor_view, context)

    assert "ID: healing_potion" in prompt
    assert "ID: gold_coin" in prompt
    assert "private_dagger" not in prompt
    assert "ruby_ring" not in prompt
    assert "{'private_dagger': 1, 'ruby_ring': 1}" not in prompt


def test_scout_system_prompt_does_not_leak_analyst_private_state():
    actor_view = _make_scout_actor_view()
    fake_character = Mock()
    fake_character.render_prompt.return_value = "BASE_PROMPT\n"
    context = {
        "speaker": "scout",
        "character": fake_character,
        "idle_banter": False,
        "current_npc_data": {"hp": 12, "active_buffs": []},
        "affection": 20,
        "flags": {"world_ready": True},
        "summary": "summary",
        "journal_events": [],
        "inventory_display_list": [],
        "has_healing_potion": False,
        "player_inv": {},
        "latest_roll": {},
        "prev_responses": [],
        "environment": {
            "current_location": "camp_center",
            "current_env_objs": actor_view.visible_environment_objects,
        },
        "entities": {
            "scout": {"inventory": {"dagger": 1}},
            "analyst": {
                "inventory": {"private_relic": 1, "mysterious_artifact": 1},
                "secret_objective": "Protect the artifact.",
            },
        },
    }

    prompt = generation._build_system_prompt(actor_view, context)

    assert "侦察员记得这场谈话。" in prompt
    assert "private_relic" not in prompt
    assert "secret_objective" not in prompt
    assert "Protect the artifact." not in prompt


def test_tactician_system_prompt_does_not_leak_peer_private_state():
    actor_view = _make_tactician_actor_view()
    fake_character = Mock()
    fake_character.render_prompt.return_value = "BASE_PROMPT\n"
    context = {
        "speaker": "tactician",
        "character": fake_character,
        "idle_banter": False,
        "current_npc_data": {"hp": 13, "active_buffs": []},
        "affection": 10,
        "flags": {"world_ready": True},
        "summary": "summary",
        "journal_events": [],
        "inventory_display_list": [],
        "has_healing_potion": False,
        "player_inv": {},
        "latest_roll": {},
        "prev_responses": [],
        "environment": {
            "current_location": "camp_center",
            "current_env_objs": actor_view.visible_environment_objects,
        },
        "entities": {
            "tactician": {"inventory": {"longsword": 1}},
            "analyst": {
                "inventory": {"private_relic": 1},
                "secret_objective": "Protect the artifact.",
            },
            "scout": {
                "inventory": {"private_dagger": 1},
                "secret_objective": "Hide unauthorized backchannel.",
            },
        },
    }

    prompt = generation._build_system_prompt(actor_view, context)

    assert "战术员记得上次交锋。" in prompt
    assert "private_relic" not in prompt
    assert "private_dagger" not in prompt
    assert "Protect the artifact." not in prompt
    assert "Hide unauthorized backchannel." not in prompt
