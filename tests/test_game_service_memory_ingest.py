import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from core.application.game_service import GameService


class _AsyncContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class _FakeGraph:
    def __init__(self, state):
        self.state = state

    async def aget_state(self, config):
        _ = config
        return SimpleNamespace(values=self.state)

    async def aupdate_state(self, config, payload, as_node):
        _ = (config, payload, as_node)

    async def ainvoke(self, payload, config):
        _ = (payload, config)
        return self.state


def test_process_chat_turn_ingests_memory_after_successful_turn():
    state = {
        "entities": {"analyst": {"hp": 10, "faction": "party"}},
        "speaker_responses": [("analyst", "……好。")],
        "journal_events": ["📦 [物品流转] player 将 1x healing_potion 交给了 analyst"],
        "current_location": "camp_fire",
        "environment_objects": {},
        "player_inventory": {},
        "flags": {},
        "turn_count": 12,
    }

    fake_memory_service = Mock()
    fake_memory_service.ingest_turn = Mock()

    service = GameService(
        saver_factory=Mock(return_value=_AsyncContextManager(object())),
        graph_builder=Mock(return_value=_FakeGraph(state)),
        initial_state_factory=Mock(return_value=state),
        memory_service=fake_memory_service,
    )

    asyncio.run(
        service.process_chat_turn(
            user_input="把药水给她",
            session_id="session-1",
        )
    )

    fake_memory_service.ingest_turn.assert_called_once()


def test_process_chat_turn_does_not_ingest_memory_when_turn_fails_before_graph():
    fake_memory_service = Mock()
    fake_memory_service.ingest_turn = Mock()
    service = GameService(
        saver_factory=Mock(),
        graph_builder=Mock(),
        initial_state_factory=Mock(),
        memory_service=fake_memory_service,
    )

    try:
        asyncio.run(service.process_chat_turn(user_input="", intent="", session_id="s1"))
    except Exception:
        pass

    fake_memory_service.ingest_turn.assert_not_called()
