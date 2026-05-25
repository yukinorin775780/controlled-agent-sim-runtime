from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional


DomainEventType = Literal[
    "actor_spoke",
    "actor_physical_action_requested",
    "actor_item_transaction_requested",
    "actor_memory_update_requested",
    "actor_affection_changed",
    "actor_negotiation_outcome_requested",
    "actor_reflection_requested",
    "actor_belief_updated",
    "world_flag_changed",
]


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: DomainEventType
    actor_id: str
    turn_index: int
    visibility: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class SocialAction:
    action_type: str
    actor_id: str
    target_actor_id: str
    item_id: str
    quantity: int
    reason: str


@dataclass(frozen=True)
class ItemTransaction:
    transaction_type: str
    from_entity: str
    to_entity: str
    item: str
    quantity: int
    accepted: bool
    reason: str


def event_to_dict(event: DomainEvent) -> Dict[str, Any]:
    return asdict(event)


def event_from_dict(payload: Dict[str, Any]) -> DomainEvent:
    return DomainEvent(
        event_id=str(payload.get("event_id") or ""),
        event_type=str(payload.get("event_type") or "actor_spoke"),  # type: ignore[arg-type]
        actor_id=str(payload.get("actor_id") or ""),
        turn_index=int(payload.get("turn_index") or 0),
        visibility=str(payload.get("visibility") or "party"),
        payload=dict(payload.get("payload") or {}),
    )


def social_action_from_payload(payload: Any, *, actor_id: str = "", reason: str = "") -> Optional[SocialAction]:
    if not isinstance(payload, dict):
        return None
    normalized_actor_id = str(payload.get("actor_id") or actor_id or "").strip().lower()
    normalized_target = str(payload.get("target_actor_id") or payload.get("recipient_id") or "").strip().lower()
    normalized_item = str(payload.get("item_id") or payload.get("item_name") or "").strip().lower()
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, quantity)
    return SocialAction(
        action_type=str(payload.get("action_type") or "").strip().lower(),
        actor_id=normalized_actor_id,
        target_actor_id=normalized_target,
        item_id=normalized_item,
        quantity=quantity,
        reason=str(payload.get("reason") or reason or "").strip(),
    )


def item_transaction_from_payload(payload: Any, *, default_reason: str = "") -> Optional[ItemTransaction]:
    if not isinstance(payload, dict):
        return None
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, quantity)
    return ItemTransaction(
        transaction_type=str(payload.get("transaction_type") or "").strip().lower(),
        from_entity=str(payload.get("from_entity") or "").strip().lower(),
        to_entity=str(payload.get("to_entity") or "").strip().lower(),
        item=str(payload.get("item") or payload.get("item_id") or "").strip().lower(),
        quantity=quantity,
        accepted=bool(payload.get("accepted", False)),
        reason=str(payload.get("reason") or default_reason or "").strip(),
    )


def item_transaction_to_transfer_payload(transaction: ItemTransaction) -> Dict[str, Any]:
    return {
        "from": transaction.from_entity,
        "to": transaction.to_entity,
        "item_id": transaction.item,
        "count": int(transaction.quantity),
    }
