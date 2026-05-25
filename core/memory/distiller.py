from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Sequence
from uuid import uuid4

from core.memory.models import MemoryRecord, MemoryScope, MemoryType, TurnMemoryInput
from core.memory.protocols import MemoryDistiller


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_memory_id() -> str:
    return f"mem_{uuid4().hex}"


def _pick_scope_from_event(event: str) -> MemoryScope:
    text = str(event or "")
    if any(token in text for token in ("🏆", "🌍", "quest_", "通关", "地图探索", "world_", "世界线")):
        return "world"
    return "party_shared"


def _pick_type_from_event(event: str) -> MemoryType:
    text = str(event or "")
    if any(token in text for token in ("战斗", "攻击", "击杀", "☠️", "🏁", "💢")):
        return "combat"
    if any(token in text for token in ("💰", "🎒", "搜刮", "装备", "药水", "healing_potion", "掉落")):
        return "relationship"
    if any(token in text for token in ("world_", "世界线")):
        return "quest"
    if any(token in text for token in ("📖", "📜", "阅读", "日记", "lore")):
        return "lore"
    if any(token in text for token in ("🏆", "🌍", "通关", "地图探索")):
        return "quest"
    return "episodic"


def _is_significant_event(event: str) -> bool:
    text = str(event or "")
    if not text:
        return False
    keywords = (
        "🏆",
        "🏁",
        "☠️",
        "💢",
        "💰",
        "🎒",
        "📦",
        "🚪",
        "🌍",
        "📖",
        "📜",
        "战斗",
        "击杀",
        "搜刮",
        "开门",
        "地图探索",
        "物品流转",
        "world_",
        "世界线",
    )
    return any(token in text for token in keywords)


def _is_memory_worthy_input(user_input: str) -> bool:
    text = str(user_input or "").strip()
    if len(text) < 2:
        return False
    keywords = (
        "给",
        "交给",
        "攻击",
        "潜行",
        "开门",
        "阅读",
        "搜刮",
        "移动",
        "施法",
        "谈判",
        "威胁",
        "神器",
        "秘密",
        "任务",
        "战斗",
        "potion",
        "artifact",
    )
    return any(token in text for token in keywords)


def _build_record(
    *,
    text: str,
    scope: MemoryScope,
    memory_type: MemoryType,
    turn_input: TurnMemoryInput,
    participants: Sequence[str] = (),
    owner_actor_id: str | None = None,
    importance: int = 1,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=_new_memory_id(),
        text=str(text or "").strip(),
        scope=scope,
        memory_type=memory_type,
        owner_actor_id=owner_actor_id,
        participants=tuple(str(item).strip().lower() for item in participants if str(item).strip()),
        location_id=str(turn_input.current_location or "unknown"),
        turn_index=int(turn_input.turn_index or 0),
        importance=max(1, int(importance)),
        source_event_ids=(),
        source_session_id=str(turn_input.session_id or ""),
        created_at=_utc_now_iso(),
    )


class RuleBasedMemoryDistiller(MemoryDistiller):
    """
    Phase 2 V1 rule-based distiller.
    Keeps deterministic behavior for stable tests and avoids LLM-induced flakiness.
    """

    def distill_turn(self, turn_input: TurnMemoryInput) -> List[MemoryRecord]:
        records: List[MemoryRecord] = []
        has_significant_event = any(_is_significant_event(item) for item in (turn_input.journal_events or []))

        # 1) Public/systems events -> world/party shared episodic memories.
        for event in (turn_input.journal_events or []):
            if not _is_significant_event(event):
                continue
            records.append(
                _build_record(
                    text=event,
                    scope=_pick_scope_from_event(event),
                    memory_type=_pick_type_from_event(event),
                    turn_input=turn_input,
                    importance=2,
                )
            )

        # 2) Player input + npc replies -> party shared episodic memory.
        user_input = str(turn_input.user_input or "").strip()
        if (
            user_input
            and len(user_input) >= 4
            and (turn_input.responses or [])
            and (_is_memory_worthy_input(user_input) or has_significant_event)
        ):
            response_preview = " ".join(
                f"{item.get('speaker', 'npc')}: {item.get('text', '')}".strip()
                for item in (turn_input.responses or [])[:2]
                if str(item.get("text", "")).strip()
            )
            summary_text = (
                f"玩家输入：{user_input}"
                + (f"；回应：{response_preview}" if response_preview else "")
            )
            records.append(
                _build_record(
                    text=summary_text,
                    scope="party_shared",
                    memory_type="episodic",
                    turn_input=turn_input,
                    importance=1,
                )
            )

        # 3) Speaker private snippets -> actor_private namespace
        if not (_is_memory_worthy_input(user_input) or has_significant_event):
            return records

        for item in (turn_input.responses or []):
            speaker = str(item.get("speaker", "")).strip().lower()
            text = str(item.get("text", "")).strip()
            if not speaker or not text:
                continue
            records.append(
                _build_record(
                    text=f"{speaker} 说：{text}",
                    scope="actor_private",
                    memory_type="episodic",
                    owner_actor_id=speaker,
                    participants=(speaker,),
                    turn_input=turn_input,
                    importance=1,
                )
            )

        return records
