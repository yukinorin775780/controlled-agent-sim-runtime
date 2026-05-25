"""
ActorViewBuilder 保护性测试。

目标：
1. 锁定 ActorView 的最小契约
2. 确保 peer private state 不泄漏
3. 确保 history / flags / memories 被裁剪后注入
"""

from dataclasses import asdict

from langchain_core.messages import AIMessage, HumanMessage

from core.actors.builders import build_actor_view
from core.actors.views import ActorView, VisibleMessage


class FakeMemoryProvider:
    def __init__(self, result=None):
        self.result = result or []
        self.calls = []

    def retrieve_for_actor(self, *, actor_id: str, query: str, top_k: int = 2):
        self.calls.append(
            {
                "actor_id": actor_id,
                "query": query,
                "top_k": top_k,
            }
        )
        return list(self.result)


def make_sample_state():
    return {
        "user_input": "你还好吗？",
        "intent": "CHAT",
        "intent_context": {
            "difficulty_class": 0,
            "reason": "normal conversation",
        },
        "is_probing_secret": False,
        "turn_count": 12,
        "time_of_day": "黄昏 (Dusk)",
        "current_location": "camp_fire",
        "flags": {
            "world_artifact_revealed": True,
            "public_box_opened": True,
            "director_route_lock": True,
            "analyst_private_doubt": True,
        },
        "latest_roll": {
            "intent": "PERSUASION",
            "dc": 12,
            "result": {"total": 15, "is_success": True},
        },
        "journal_events": [
            "old-1",
            "old-2",
            "old-3",
            "old-4",
            "old-5",
            "old-6",
            "old-7",
            "old-8",
            "new-public-1",
            "new-public-2",
        ],
        "environment_objects": {
            "iron_chest": {
                "name": "Iron Chest",
                "status": "locked",
                "description": "A heavy iron chest.",
            },
            "hidden_trap": {
                "name": "Hidden Trap",
                "status": "hidden",
                "description": "Should not be visible by default.",
                "entity_type": "trap",
                "is_hidden": True,
            },
        },
        "messages": [
            HumanMessage(content="我们聊聊。"),
            AIMessage(content="[analyst]: ……说吧。", name="analyst"),
            {"role": "user", "content": "你信任我吗？"},
            {"role": "assistant", "content": "[scout]: 真是个危险的问题。", "name": "scout"},
        ],
        "entities": {
            "player": {
                "name": "玩家",
                "hp": 20,
                "max_hp": 20,
                "inventory": {"gold": 15},
                "position": "camp_fire",
                "status": "alive",
                "faction": "party",
            },
            "analyst": {
                "name": "Analyst",
                "hp": 10,
                "max_hp": 10,
                "inventory": {"healing_potion": 1, "torch": 1},
                "affection": 55,
                "active_buffs": [{"id": "bless", "duration": 2}],
                "position": "camp_fire",
                "status": "alive",
                "faction": "party",
                "dynamic_states": {
                    "protocol_confidence": {"current_value": 80},
                    "memory_awakening": {"current_value": 20},
                },
                "secret_objective": "Protect the artifact.",
            },
            "scout": {
                "name": "Scout",
                "hp": 12,
                "max_hp": 12,
                "inventory": {"dagger": 1},
                "affection": 10,
                "active_buffs": [],
                "position": "camp_fire",
                "status": "alive",
                "faction": "party",
                "dynamic_states": {
                    "autonomy_pressure": {"current_value": 85},
                },
                "secret_objective": "Hide unauthorized backchannel.",
            },
        },
    }


def test_build_actor_view_returns_contract_shape():
    state = make_sample_state()
    memory_provider = FakeMemoryProvider(result=["她记得玩家曾帮她挡刀。"])

    actor_view = build_actor_view(
        state,
        "analyst",
        memory_provider=memory_provider,
    )

    assert isinstance(actor_view, ActorView)
    assert actor_view.actor_id == "analyst"
    assert actor_view.user_input == "你还好吗？"
    assert actor_view.intent == "CHAT"
    assert actor_view.current_location == "camp_fire"
    assert actor_view.time_of_day == "黄昏 (Dusk)"
    assert actor_view.turn_count == 12
    assert actor_view.memory_snippets == ["她记得玩家曾帮她挡刀。"]

    assert actor_view.self_state.actor_id == "analyst"
    assert actor_view.self_state.inventory == {"healing_potion": 1, "torch": 1}
    assert actor_view.self_state.affection == 55
    assert actor_view.self_state.dynamic_states == {
        "protocol_confidence": 80,
        "memory_awakening": 20,
    }


def test_build_actor_view_hides_peer_private_fields():
    state = make_sample_state()

    actor_view = build_actor_view(state, "analyst")

    peer_view = actor_view.other_entities["scout"]

    assert peer_view.entity_id == "scout"
    assert peer_view.name == "Scout"
    assert peer_view.position == "camp_fire"
    assert peer_view.status == "alive"
    assert peer_view.faction == "party"

    assert not hasattr(peer_view, "inventory")
    assert not hasattr(peer_view, "affection")
    assert not hasattr(peer_view, "dynamic_states")
    assert not hasattr(peer_view, "secret_objective")


def test_build_actor_view_keeps_self_private_fields_only_on_self_state():
    state = make_sample_state()

    actor_view = build_actor_view(state, "analyst")

    self_dict = asdict(actor_view.self_state)

    assert "inventory" in self_dict
    assert "affection" in self_dict
    assert "dynamic_states" in self_dict

    for peer in actor_view.other_entities.values():
        peer_dict = asdict(peer)
        assert "inventory" not in peer_dict
        assert "affection" not in peer_dict
        assert "dynamic_states" not in peer_dict
        assert "secret_objective" not in peer_dict


def test_build_actor_view_filters_flags_to_public_only():
    state = make_sample_state()

    actor_view = build_actor_view(state, "analyst")

    assert actor_view.visible_flags == {
        "world_artifact_revealed": True,
        "public_box_opened": True,
    }
    assert "director_route_lock" not in actor_view.visible_flags
    assert "analyst_private_doubt" not in actor_view.visible_flags


def test_build_actor_view_policy_flags_do_not_leak_visibility_metadata():
    state = make_sample_state()
    state["flags"] = {
        "world_artifact_revealed": True,
        "analyst_artifact_secret": {
            "value": True,
            "visibility": {"scope": "actor", "actors": ["analyst"], "reason": "personal_secret"},
            "hidden_metadata": {"internal_id": "sec-01"},
        },
    }

    analyst_view = build_actor_view(state, "analyst")
    scout_view = build_actor_view(state, "scout")

    assert analyst_view.visible_flags["analyst_artifact_secret"] is True
    assert "hidden_metadata" not in analyst_view.visible_flags
    assert "visibility" not in analyst_view.visible_flags
    assert "analyst_artifact_secret" not in scout_view.visible_flags


def test_build_actor_view_normalizes_history_messages():
    state = make_sample_state()

    actor_view = build_actor_view(state, "analyst")

    assert actor_view.visible_history
    assert all(isinstance(msg, VisibleMessage) for msg in actor_view.visible_history)

    assert actor_view.visible_history[0].role == "user"
    assert actor_view.visible_history[0].content == "我们聊聊。"

    assert actor_view.visible_history[1].role == "assistant"
    assert actor_view.visible_history[1].speaker_id == "analyst"
    assert "……说吧" in actor_view.visible_history[1].content


def test_build_actor_view_limits_recent_public_events():
    state = make_sample_state()

    actor_view = build_actor_view(state, "analyst")

    assert actor_view.recent_public_events == [
        "old-3",
        "old-4",
        "old-5",
        "old-6",
        "old-7",
        "old-8",
        "new-public-1",
        "new-public-2",
    ]


def test_build_actor_view_calls_memory_provider_with_actor_scope():
    state = make_sample_state()
    memory_provider = FakeMemoryProvider(result=["memory-a", "memory-b"])

    actor_view = build_actor_view(
        state,
        "analyst",
        memory_provider=memory_provider,
    )

    assert actor_view.memory_snippets == ["memory-a", "memory-b"]
    assert memory_provider.calls == [
        {
            "actor_id": "analyst",
            "query": "你还好吗？",
            "top_k": 2,
        }
    ]


def test_build_actor_view_skips_memory_lookup_for_blank_input():
    state = make_sample_state()
    state["user_input"] = "   "
    memory_provider = FakeMemoryProvider(result=["should-not-be-used"])

    actor_view = build_actor_view(
        state,
        "analyst",
        memory_provider=memory_provider,
    )

    assert actor_view.memory_snippets == []
    assert memory_provider.calls == []


def test_build_actor_view_supports_legacy_npc_inventory_fallback():
    state = make_sample_state()
    state["entities"]["analyst"]["inventory"] = None
    state["npc_inventory"] = {"healing_potion": 2}

    actor_view = build_actor_view(state, "analyst")

    assert actor_view.self_state.inventory == {"healing_potion": 2}


def test_build_actor_view_never_leaks_internal_graph_fields():
    state = make_sample_state()
    state["speaker_queue"] = ["scout", "analyst"]
    state["final_response"] = "internal only"
    state["thought_process"] = "private monologue"

    actor_view = build_actor_view(state, "analyst")
    actor_dict = asdict(actor_view)

    assert "speaker_queue" not in actor_dict
    assert "final_response" not in actor_dict
    assert "thought_process" not in actor_dict


def test_hidden_trap_metadata_is_filtered_until_revealed():
    state = make_sample_state()
    state["environment_objects"]["gas_trap_1"] = {
        "id": "gas_trap_1",
        "type": "trap",
        "name": "毒气陷阱",
        "is_hidden": True,
        "detect_dc": 13,
        "disarm_dc": 15,
    }

    hidden_view = build_actor_view(state, "player")
    assert "gas_trap_1" not in hidden_view.visible_environment_objects

    state["environment_objects"]["gas_trap_1"]["is_hidden"] = False
    state["environment_objects"]["gas_trap_1"]["status"] = "revealed"
    revealed_view = build_actor_view(state, "player")
    assert revealed_view.visible_environment_objects["gas_trap_1"]["status"] == "revealed"
