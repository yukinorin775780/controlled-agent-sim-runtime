"""
generation 节点保护性测试。
锁定工具循环、JSON 动作解析和 orchestrator 调度行为。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import core.graph.nodes.generation as generation


def test_extract_inventory_states_prefers_entity_inventory_and_supports_fallback():
    state = {
        "player_inventory": {"gold": 5},
        "npc_inventory": {"healing_potion": 2, "torch": 1},
    }

    result_from_fallback = generation._extract_inventory_states(
        state=state,
        current_npc={"inventory": None},
    )
    result_from_entity = generation._extract_inventory_states(
        state=state,
        current_npc={"inventory": {"healing_potion": 1, "dagger": 1}},
    )

    assert result_from_fallback["player_inv"] == {"gold": 5}
    assert result_from_fallback["npc_inv"] == {"healing_potion": 2, "torch": 1}
    assert result_from_fallback["has_healing_potion"] is True
    assert result_from_entity["npc_inv"] == {"healing_potion": 1, "dagger": 1}
    assert result_from_entity["has_healing_potion"] is True


def test_process_dialogue_triggers_updates_affection_and_entity_snapshot():
    entities = {"analyst": {"affection": 10, "inventory": {}}}
    flags = {"shared_secret": True}

    with patch(
        "core.graph.nodes.generation.process_dialogue_triggers",
        return_value={"journal_entries": ["approval up"], "relationship_delta": 4},
    ) as process_triggers:
        result = generation._process_dialogue_triggers(
            user_input="我把药水给你。",
            triggers_config=[{"keyword": "药水"}],
            flags=flags,
            player_inv={"gold": 5},
            npc_inv={"healing_potion": 1},
            affection=10,
            speaker="analyst",
            entities=entities,
        )

    process_triggers.assert_called_once()
    assert result["affection"] == 14
    assert result["trigger_result"] == {
        "journal_entries": ["approval up"],
        "relationship_delta": 4,
    }
    assert result["entities"]["analyst"]["affection"] == 14
    assert result["entities"]["analyst"]["inventory"] == {"healing_potion": 1}


def test_build_environmental_awareness_collects_location_and_object_snapshot():
    state = {
        "current_location": "camp_fire",
        "environment_objects": {
            "iron_chest": {"status": "locked"},
            "invalid": "ignore-me",
        },
    }

    awareness = generation._build_environmental_awareness(state)

    assert awareness["current_location"] == "camp_fire"
    assert awareness["current_env_objs"] == {"iron_chest": {"status": "locked"}}
    assert awareness["current_env_objs"] is not state["environment_objects"]


def test_build_history_dicts_injects_physical_action_suffix_verbatim():
    state = {"messages": []}
    context = {
        "user_input": "把药水给我。",
        "is_first_npc_of_player_turn": True,
        "idle_banter": False,
        "intent": "use_item",
        "speaker": "analyst",
        "npc_inv": {"healing_potion": 1, "torch": 1},
        "prev_responses": [],
    }

    history_dicts = generation._build_history_dicts(state, context)

    assert len(history_dicts) == 1
    content = history_dicts[0]["content"]
    assert content.startswith("把药水给我。")
    assert "\n*(现在轮到你做出反应了)*" in content
    assert "🚨 [CRITICAL OVERRIDE - PHYSICAL ACTION REQUIRED]:" in content
    assert '[YOUR ABSOLUTE TRUTH]: Your physical inventory exactly contains: {\'healing_potion\': 1, \'torch\': 1}.' in content
    assert '1. Taking an item: "physical_action": {"action_type": "transfer_item", "source_id": "player", "target_id": "analyst", "item_id": "healing_potion", "amount": 1}' in content
    assert content.endswith("IF YOU DO NOT INCLUDE THIS FIELD IN YOUR JSON, YOU ARE JUST STANDING STILL AND DOING NOTHING!")


def test_build_history_dicts_preserves_a_to_a_recency_injection_verbatim():
    state = {"messages": [{"role": "user", "content": "你怎么看？"}]}
    context = {
        "user_input": "你怎么看？",
        "is_first_npc_of_player_turn": False,
        "idle_banter": False,
        "intent": "chat",
        "speaker": "scout",
        "npc_inv": {},
        "prev_responses": [("analyst", "别碰那个圣徽。")],
    }

    history_dicts = generation._build_history_dicts(state, context)

    assert history_dicts == [
        {
            "role": "user",
            "content": "[事件回顾] 玩家说：你怎么看？\n[刚刚发生] analyst 回应道：别碰那个圣徽。\n*(现在轮到你做出反应了)*",
        }
    ]


def test_execute_llm_with_tools_resolves_inventory_lookup():
    lc_messages = [HumanMessage(content="把药水给我。")]
    llm_with_tools = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "check_target_inventory",
                            "args": {"target_id": "player", "item_keyword": "healing"},
                        }
                    ],
                ),
                AIMessage(content='{"reply":"拿去。"}'),
            ]
        )
    )
    player_inventory = {"healing_potion": 2}
    current_entities = {"analyst": {"inventory": {}}}
    fake_registry = SimpleNamespace(get_name=lambda item_id: item_id.replace("_", " "))

    with patch("core.graph.nodes.generation.get_registry", return_value=fake_registry):
        response, updated_messages = asyncio.run(
            generation._execute_llm_with_tools(
                llm_with_tools=llm_with_tools,
                lc_messages=lc_messages,
                player_inv_for_physics=player_inventory,
                current_entities=current_entities,
                idle_banter=False,
            )
        )

    assert response.content == '{"reply":"拿去。"}'
    assert any(isinstance(message, ToolMessage) for message in updated_messages)
    tool_message = next(
        message for message in updated_messages if isinstance(message, ToolMessage)
    )
    assert "player 拥有 2 个 healing_potion" in tool_message.content


def test_parse_and_apply_actions_updates_state_and_executes_physical_action():
    current_entities = {
        "analyst": {
            "affection": 10,
            "inventory": {},
            "protocol_confidence": 60,
            "memory_awakening": 20,
        }
    }
    player_inventory = {"healing_potion": 1}
    current_env_objects = {"iron_chest": {"status": "locked"}}
    raw_output = """
    ```json
    {
      "reply": "[Analyst]说： 风停了。",
      "internal_monologue": "她开始动摇。",
      "state_changes": {
        "affection_delta": 5,
        "protocol_confidence_delta": -10,
        "memory_awakening_delta": 15
      },
      "physical_action": {
        "action_type": "consume",
        "item_id": "healing_potion"
      }
    }
    ```
    """

    with patch(
        "core.graph.nodes.generation._execute_json_action",
        return_value=["healing_potion consumed"],
    ) as execute_action:
        result = generation._parse_and_apply_actions(
            raw_output=raw_output,
            idle_banter=False,
            speaker="analyst",
            entities={"analyst": {"inventory": {}}},
            current_entities=current_entities,
            player_inv_for_physics=player_inventory,
            current_env_objs=current_env_objects,
        )

    execute_action.assert_called_once()
    assert result["clean_text"] == "风停了。"
    assert result["thought_process"] == "她开始动摇。"
    assert result["tool_physics_events"] == ["healing_potion consumed"]
    assert result["state_changes_applied"] is True
    assert current_entities["analyst"]["affection"] == 15
    assert current_entities["analyst"]["protocol_confidence"] == 50
    assert current_entities["analyst"]["memory_awakening"] == 35


def test_generation_node_delegates_to_helpers_and_preserves_output_shape():
    node = generation.create_generation_node()
    state = {
        "entities": {"analyst": {"hp": 20, "affection": 8, "inventory": {}}},
        "current_speaker": "analyst",
        "user_input": "你好。",
        "speaker_responses": [],
        "messages": [],
    }
    fake_character = Mock()
    helper_context = {
        "entities": {"analyst": {"hp": 20, "affection": 8, "inventory": {}}},
        "speaker": "analyst",
        "character": fake_character,
        "user_input": "你好。",
        "prev_responses": [],
        "is_first_npc_of_player_turn": True,
        "idle_banter": False,
        "is_banter": False,
        "dm_text": "",
        "latest_roll": None,
        "trigger_result": {"journal_entries": []},
        "triggers_config": [],
        "flags": {},
        "player_inv_for_physics": {},
        "current_env_objs": {},
        "current_entities": {
            "analyst": {"hp": 20, "affection": 8, "inventory": {}, "position": "camp_center"}
        },
        "history_dicts": [{"role": "user", "content": "你好。"}],
    }
    parsed_actions = {
        "clean_text": "你好，旅者。",
        "thought_process": "保持戒备。",
        "tool_physics_events": [],
        "state_changes_applied": False,
        "idle_merged": None,
    }

    with patch("characters.loader.load_character", return_value=fake_character), patch(
        "core.graph.nodes.generation._prepare_generation_context",
        return_value=helper_context,
    ) as prepare_context, patch(
        "core.graph.nodes.generation._build_system_prompt",
        return_value="SYSTEM PROMPT",
    ) as build_prompt, patch(
        "core.graph.nodes.generation._build_lc_messages",
        return_value=[HumanMessage(content="你好。")],
    ) as build_messages, patch(
        "core.graph.nodes.generation._create_llm_client",
        return_value=SimpleNamespace(ainvoke=AsyncMock()),
    ) as create_llm, patch(
        "core.graph.nodes.generation._execute_llm_with_tools",
        return_value=(AIMessage(content='{"reply":"你好，旅者。"}'), [HumanMessage(content="你好。")]),
    ) as execute_llm, patch(
        "core.graph.nodes.generation._parse_and_apply_actions",
        return_value=parsed_actions,
    ) as parse_actions:
        result = asyncio.run(node(state))

    prepare_context.assert_called_once()
    build_prompt.assert_called_once()
    build_messages.assert_called_once()
    create_llm.assert_called_once()
    execute_llm.assert_called_once()
    parse_actions.assert_called_once()
    assert result == {
        "final_response": "你好，旅者。",
        "speaker_responses": [("analyst", "你好，旅者。")],
        "thought_process": "保持戒备。",
        "messages": [
            HumanMessage(content="你好。"),
            AIMessage(content="[analyst]: 你好，旅者。", name="analyst"),
        ],
        "entities": {
            "analyst": {
                "hp": 20,
                "affection": 8,
                "inventory": {},
                "position": "camp_center",
            }
        },
    }


def test_generation_node_falls_back_when_current_speaker_is_player():
    node = generation.create_generation_node()
    state = {
        "entities": {"player": {"hp": 20}, "analyst": {"hp": 10, "inventory": {}}},
        "current_speaker": "player",
        "speaker_queue": ["analyst"],
        "user_input": "你好。",
        "speaker_responses": [],
        "messages": [],
    }
    fake_character = Mock()
    helper_context = {
        "entities": {"analyst": {"hp": 10, "inventory": {}}},
        "speaker": "analyst",
        "character": fake_character,
        "user_input": "你好。",
        "prev_responses": [],
        "is_first_npc_of_player_turn": True,
        "idle_banter": False,
        "is_banter": False,
        "dm_text": "",
        "latest_roll": None,
        "trigger_result": {"journal_entries": []},
        "triggers_config": [],
        "flags": {},
        "player_inv_for_physics": {},
        "current_env_objs": {},
        "current_entities": {
            "analyst": {"hp": 10, "affection": 0, "inventory": {}, "position": "camp_center"}
        },
        "history_dicts": [{"role": "user", "content": "你好。"}],
    }
    parsed_actions = {
        "clean_text": "你好。",
        "thought_process": "",
        "tool_physics_events": [],
        "state_changes_applied": False,
        "idle_merged": None,
    }
    loaded_names: list[str] = []

    def _fake_load_character(name: str):
        loaded_names.append(name)
        if name == "player":
            raise AssertionError("generation must not load player.yaml")
        return fake_character

    with patch("characters.loader.load_character", side_effect=_fake_load_character), patch(
        "core.graph.nodes.generation._prepare_generation_context",
        return_value=helper_context,
    ), patch(
        "core.graph.nodes.generation._build_system_prompt",
        return_value="SYSTEM PROMPT",
    ), patch(
        "core.graph.nodes.generation._build_lc_messages",
        return_value=[HumanMessage(content="你好。")],
    ), patch(
        "core.graph.nodes.generation._create_llm_client",
        return_value=SimpleNamespace(ainvoke=AsyncMock()),
    ), patch(
        "core.graph.nodes.generation._execute_llm_with_tools",
        return_value=(AIMessage(content='{"reply":"你好。"}'), [HumanMessage(content="你好。")]),
    ), patch(
        "core.graph.nodes.generation._parse_and_apply_actions",
        return_value=parsed_actions,
    ):
        result = asyncio.run(node(state))

    assert result["speaker_responses"][0][0] == "analyst"
    assert "player" not in loaded_names
    assert "analyst" in loaded_names


def test_generation_node_does_not_load_character_for_environment_door_target():
    node = generation.create_generation_node()
    state = {
        "entities": {
            "player": {"hp": 20},
            "heavy_oak_door_1": {"entity_type": "door", "status": "closed"},
            "analyst": {"hp": 10, "inventory": {}},
        },
        "current_speaker": "heavy_oak_door_1",
        "speaker_queue": ["analyst"],
        "user_input": "打开门",
        "speaker_responses": [],
        "messages": [],
    }
    fake_character = Mock()
    helper_context = {
        "entities": {"analyst": {"hp": 10, "inventory": {}}},
        "speaker": "analyst",
        "character": fake_character,
        "user_input": "打开门",
        "prev_responses": [],
        "is_first_npc_of_player_turn": True,
        "idle_banter": False,
        "is_banter": False,
        "dm_text": "",
        "latest_roll": None,
        "trigger_result": {"journal_entries": []},
        "triggers_config": [],
        "flags": {},
        "player_inv_for_physics": {},
        "current_env_objs": {},
        "current_entities": {
            "analyst": {"hp": 10, "affection": 0, "inventory": {}, "position": "camp_center"}
        },
        "history_dicts": [{"role": "user", "content": "打开门"}],
    }
    parsed_actions = {
        "clean_text": "明白。",
        "thought_process": "",
        "tool_physics_events": [],
        "state_changes_applied": False,
        "idle_merged": None,
    }
    loaded_names: list[str] = []

    def _fake_load_character(name: str):
        loaded_names.append(name)
        if name == "heavy_oak_door_1":
            raise AssertionError("generation must not load heavy_oak_door_1.yaml")
        return fake_character

    with patch("characters.loader.load_character", side_effect=_fake_load_character), patch(
        "core.graph.nodes.generation._prepare_generation_context",
        return_value=helper_context,
    ), patch(
        "core.graph.nodes.generation._build_system_prompt",
        return_value="SYSTEM PROMPT",
    ), patch(
        "core.graph.nodes.generation._build_lc_messages",
        return_value=[HumanMessage(content="打开门")],
    ), patch(
        "core.graph.nodes.generation._create_llm_client",
        return_value=SimpleNamespace(ainvoke=AsyncMock()),
    ), patch(
        "core.graph.nodes.generation._execute_llm_with_tools",
        return_value=(AIMessage(content='{"reply":"明白。"}'), [HumanMessage(content="打开门")]),
    ), patch(
        "core.graph.nodes.generation._parse_and_apply_actions",
        return_value=parsed_actions,
    ):
        result = asyncio.run(node(state))

    assert result["speaker_responses"][0][0] == "analyst"
    assert "heavy_oak_door_1" not in loaded_names
    assert "analyst" in loaded_names
