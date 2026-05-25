from core.memory.distiller import RuleBasedMemoryDistiller
from core.memory.models import TurnMemoryInput


def test_distiller_emits_party_shared_memory_for_public_gift():
    distiller = RuleBasedMemoryDistiller()

    records = distiller.distill_turn(
        TurnMemoryInput(
            session_id="s1",
            user_input="把这瓶药水给分析员",
            responses=[{"speaker": "analyst", "text": "……我收下了。"}],
            journal_events=["📦 [物品流转] player 将 1x healing_potion 交给了 analyst"],
            current_location="camp_fire",
            turn_index=12,
            party_status={},
            flags={},
        )
    )

    assert len(records) >= 1
    assert any(record.scope == "party_shared" for record in records)
    assert any("药水" in record.text or "healing_potion" in record.text for record in records)


def test_distiller_emits_world_memory_for_public_flag_change():
    distiller = RuleBasedMemoryDistiller()

    records = distiller.distill_turn(
        TurnMemoryInput(
            session_id="s1",
            user_input="我知道神器的事了",
            responses=[],
            journal_events=["📜 [系统] 剧情世界线已变动: ['world_artifact_revealed']"],
            current_location="camp_fire",
            turn_index=13,
            party_status={},
            flags={"world_artifact_revealed": True},
        )
    )

    assert any(record.scope == "world" for record in records)
    assert any(record.memory_type == "quest" for record in records)


def test_distiller_returns_empty_for_small_talk():
    distiller = RuleBasedMemoryDistiller()

    records = distiller.distill_turn(
        TurnMemoryInput(
            session_id="s1",
            user_input="今天天气不错",
            responses=[{"speaker": "analyst", "text": "……也许吧。"}],
            journal_events=[],
            current_location="camp_fire",
            turn_index=14,
            party_status={},
            flags={},
        )
    )

    assert records == []
