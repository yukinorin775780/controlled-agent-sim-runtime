from __future__ import annotations

from typing import Dict, Any

from core.actors.executor import process_reflection_queue
from core.actors.registry import get_default_actor_registry


async def run_reflection_tick(state: Dict[str, Any], max_items: int = 1) -> Dict[str, Any]:
    return await process_reflection_queue(
        state=state,
        registry=get_default_actor_registry(),
        max_items=max_items,
    )

