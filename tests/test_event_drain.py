from core.events.models import DomainEvent
from core.graph.nodes.event_drain import event_drain_node


def test_event_drain_turns_actor_spoke_event_into_messages_and_responses():
    state = {
        "pending_events": [
            DomainEvent(
                event_id="evt-1",
                event_type="actor_spoke",
                actor_id="analyst",
                turn_index=12,
                visibility="party",
                payload={"text": "别碰那个圣徽。"},
            )
        ],
        "speaker_responses": [],
        "messages": [],
    }

    result = event_drain_node(state)

    assert result["pending_events"] == []
    assert result["speaker_responses"] == [("analyst", "别碰那个圣徽。")]
    assert len(result["messages"]) == 1


def test_event_drain_turns_world_flag_changed_event_into_flags_patch():
    state = {
        "pending_events": [
            DomainEvent(
                event_id="evt-2",
                event_type="world_flag_changed",
                actor_id="director",
                turn_index=12,
                visibility="world",
                payload={"flag": "world_artifact_revealed", "value": True},
            )
        ],
        "flags": {},
    }

    result = event_drain_node(state)

    assert result["pending_events"] == []
    assert result["flags"]["world_artifact_revealed"] is True


def test_event_drain_does_not_process_reflection_queue_when_no_pending_events():
    state = {
        "pending_events": [],
        "reflection_queue": [
            {
                "actor_id": "scout",
                "reason": "defer_until_runtime_ready",
                "priority": 2,
                "source_turn": 9,
                "payload": {},
            }
        ],
    }

    result = event_drain_node(state)

    assert result == {"pending_events": []}
    assert len(state["reflection_queue"]) == 1


def test_event_drain_applies_accepted_item_transaction_to_inventories():
    state = {
        "pending_events": [
            DomainEvent(
                event_id="evt-transfer",
                event_type="actor_item_transaction_requested",
                actor_id="analyst",
                turn_index=5,
                visibility="party",
                payload={
                    "social_action": {
                        "action_type": "gift_accept",
                        "actor_id": "analyst",
                        "target_actor_id": "player",
                        "item_id": "healing_potion",
                        "quantity": 1,
                        "reason": "accepted_gift",
                    },
                    "transaction": {
                        "transaction_type": "transfer",
                        "from_entity": "player",
                        "to_entity": "analyst",
                        "item": "healing_potion",
                        "quantity": 1,
                        "accepted": True,
                        "reason": "accepted_gift",
                    },
                },
            )
        ],
        "entities": {"analyst": {"inventory": {"healing_potion": 1}}},
        "player_inventory": {"healing_potion": 2},
    }

    result = event_drain_node(state)

    assert result["pending_events"] == []
    assert result["player_inventory"]["healing_potion"] == 1
    assert result["entities"]["analyst"]["inventory"]["healing_potion"] == 2


def test_event_drain_rejected_item_transaction_keeps_player_inventory_intact():
    state = {
        "pending_events": [
            DomainEvent(
                event_id="evt-reject",
                event_type="actor_item_transaction_requested",
                actor_id="scout",
                turn_index=5,
                visibility="party",
                payload={
                    "social_action": {
                        "action_type": "gift_reject",
                        "actor_id": "scout",
                        "target_actor_id": "player",
                        "item_id": "healing_potion",
                        "quantity": 1,
                        "reason": "unwanted_gift",
                    },
                    "transaction": {
                        "transaction_type": "no_op",
                        "from_entity": "player",
                        "to_entity": "scout",
                        "item": "healing_potion",
                        "quantity": 1,
                        "accepted": False,
                        "reason": "unwanted_gift",
                    },
                },
            )
        ],
        "entities": {"scout": {"inventory": {}}},
        "player_inventory": {"healing_potion": 2},
    }

    result = event_drain_node(state)

    assert result["pending_events"] == []
    assert result["player_inventory"]["healing_potion"] == 2
    assert result["entities"]["scout"]["inventory"] == {}


def test_event_drain_supports_actor_private_and_party_shared_memory_scopes():
    state = {
        "pending_events": [
            DomainEvent(
                event_id="evt-memory-private",
                event_type="actor_memory_update_requested",
                actor_id="player",
                turn_index=7,
                visibility="actor",
                payload={
                    "scope": "actor_private",
                    "text": "我读懂了 Gatekeeper 与毒气陷阱的关联。",
                },
            ),
            DomainEvent(
                event_id="evt-memory-party",
                event_type="actor_memory_update_requested",
                actor_id="player",
                turn_index=7,
                visibility="party",
                payload={
                    "scope": "party_shared",
                    "text": "队伍已知 heavy_iron_key 与逃生有关。",
                },
            ),
        ],
        "actor_runtime_state": {},
    }

    result = event_drain_node(state)

    assert result["pending_events"] == []
    assert "我读懂了 Gatekeeper 与毒气陷阱的关联。" in result["actor_runtime_state"]["player"]["memory_notes"]
    assert (
        "队伍已知 heavy_iron_key 与逃生有关。"
        in result["actor_runtime_state"]["__party_shared__"]["memory_notes"]
    )
