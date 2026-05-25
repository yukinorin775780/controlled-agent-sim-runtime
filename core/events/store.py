from __future__ import annotations

from typing import Any, Dict, List

from core.events.models import DomainEvent, event_to_dict


def append_pending_events(state: Dict[str, Any], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pending = list(state.get("pending_events") or [])
    pending.extend(dict(item) for item in events if isinstance(item, dict))
    return pending


def drain_pending_events(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    pending = list(state.get("pending_events") or [])
    drained: List[Dict[str, Any]] = []
    for item in pending:
        if isinstance(item, dict):
            drained.append(dict(item))
            continue
        if isinstance(item, DomainEvent):
            drained.append(event_to_dict(item))
    return drained
