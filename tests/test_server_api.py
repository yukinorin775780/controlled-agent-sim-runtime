"""
FastAPI 路由保护性测试。
锁定 /api/chat 的轻量委托与错误映射。
"""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import server
from core.application.game_service import InvalidChatRequestError


def test_chat_endpoint_delegates_to_service_and_preserves_response_schema():
    expected_payload = {
        "responses": [{"speaker": "analyst", "text": "别碰那个圣徽。"}],
        "journal_events": ["Story advanced"],
        "current_location": "camp_center",
        "environment_objects": {"iron_chest": {"status": "locked"}},
        "party_status": {"analyst": {"hp": 10}},
        "player_inventory": {"healing_potion": 2},
        "combat_state": {
            "combat_active": True,
            "initiative_order": ["player", "drone_1"],
            "current_turn_index": 0,
            "turn_resources": {"player": {"action": 1, "bonus_action": 1, "movement": 6}},
        },
    }
    original_service = server.game_service
    mock_service = AsyncMock()
    mock_service.process_chat_turn.return_value = expected_payload
    server.game_service = mock_service

    try:
        client = TestClient(server.app)
        response = client.post(
            "/api/chat",
            json={
                "user_input": "你好",
                "intent": "chat",
                "session_id": "session-1",
                "character": "analyst",
                "map_id": "hazard_lab",
            },
        )
    finally:
        server.game_service = original_service

    assert response.status_code == 200
    assert response.json() == expected_payload
    mock_service.process_chat_turn.assert_awaited_once_with(
        user_input="你好",
        intent="chat",
        session_id="session-1",
        character="analyst",
        map_id="hazard_lab",
        target=None,
        source=None,
        client_player_position=None,
        player_position=None,
    )


def test_chat_endpoint_maps_service_validation_error_to_http_400():
    original_service = server.game_service
    server.game_service = AsyncMock()
    server.game_service.process_chat_turn.side_effect = InvalidChatRequestError(
        "Unknown character: analyst"
    )

    try:
        client = TestClient(server.app)
        response = client.post(
            "/api/chat",
            json={"user_input": "loot", "intent": "ui_action_loot", "session_id": "s-1"},
        )
    finally:
        server.game_service = original_service

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown character: analyst"}


def test_state_endpoint_forwards_optional_map_id_to_game_service():
    original_service = server.game_service
    expected = {"game_state": {"current_location": "危害研究员的废弃实验室"}}
    mock_service = AsyncMock()
    mock_service.get_state_snapshot.return_value = expected
    server.game_service = mock_service

    try:
        client = TestClient(server.app)
        response = client.get(
            "/api/state",
            params={"session_id": "session-map", "map_id": "hazard_lab"},
        )
    finally:
        server.game_service = original_service

    assert response.status_code == 200
    assert response.json() == expected
    mock_service.get_state_snapshot.assert_awaited_once_with(
        session_id="session-map",
        map_id="hazard_lab",
    )


def test_chat_endpoint_forwards_target_and_source_to_game_service():
    expected_payload = {
        "responses": [],
        "journal_events": [],
        "current_location": "camp_center",
        "environment_objects": {},
        "party_status": {},
        "player_inventory": {},
        "combat_state": {
            "combat_active": False,
            "initiative_order": [],
            "current_turn_index": 0,
            "turn_resources": {},
        },
    }
    original_service = server.game_service
    mock_service = AsyncMock()
    mock_service.process_chat_turn.return_value = expected_payload
    server.game_service = mock_service

    try:
        client = TestClient(server.app)
        response = client.post(
            "/api/chat",
            json={
                "user_input": "",
                "intent": "INTERACT",
                "session_id": "session-target",
                "character": "player",
                "map_id": "hazard_lab",
                "target": "heavy_oak_door_1",
                "source": "interaction",
                "client_player_position": {"x": 17, "y": 4},
                "player_position": [17, 4],
            },
        )
    finally:
        server.game_service = original_service

    assert response.status_code == 200
    mock_service.process_chat_turn.assert_awaited_once_with(
        user_input="",
        intent="INTERACT",
        session_id="session-target",
        character="player",
        map_id="hazard_lab",
        target="heavy_oak_door_1",
        source="interaction",
        client_player_position={"x": 17, "y": 4},
        player_position=[17, 4],
    )


def test_reset_endpoint_reinitializes_session_with_map_id():
    expected_payload = {
        "responses": [],
        "journal_events": [],
        "current_location": "危害研究员的废弃实验室",
        "environment_objects": {},
        "party_status": {"player": {"hp": 20}},
        "player_inventory": {"healing_potion": 2},
        "combat_state": {
            "combat_active": False,
            "initiative_order": [],
            "current_turn_index": 0,
            "turn_resources": {},
        },
    }
    original_service = server.game_service
    mock_service = AsyncMock()
    mock_service.reset_session.return_value = expected_payload
    server.game_service = mock_service

    try:
        client = TestClient(server.app)
        response = client.post(
            "/api/reset",
            json={"session_id": "session-reset", "map_id": "hazard_lab"},
        )
    finally:
        server.game_service = original_service

    assert response.status_code == 200
    assert response.json() == expected_payload
    mock_service.reset_session.assert_awaited_once_with(
        session_id="session-reset",
        map_id="hazard_lab",
    )


def test_server_bind_prefers_environment_port(monkeypatch):
    monkeypatch.delenv("CONTROLLED_AGENT_HOST", raising=False)
    monkeypatch.setenv("CONTROLLED_AGENT_PORT", "8123")
    host, port = server._resolve_server_bind([])
    assert host == "127.0.0.1"
    assert port == 8123
