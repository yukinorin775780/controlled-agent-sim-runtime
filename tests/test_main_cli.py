"""
CLI 主循环保护性测试。
锁定 main.py 对 GameService 的调用与关键渲染分支。
"""

import asyncio
from unittest.mock import ANY, AsyncMock

import main


class _FakeUI:
    def __init__(self):
        self.system_info = []
        self.errors = []
        self.npc_streams = []
        self.dm_narrations = []
        self.dice_rolls = []

    def print_system_info(self, message):
        self.system_info.append(message)

    def print_error(self, message):
        self.errors.append(message)

    async def print_npc_response_stream(self, speaker, text, char_delay=0.03):
        self.npc_streams.append((speaker, text, char_delay))

    def print_dm_narration(self, text):
        self.dm_narrations.append(text)

    async def show_dice_roll_animation(self, intent, dc, modifier, roll_data):
        self.dice_rolls.append((intent, dc, modifier, roll_data))


def test_execute_cli_turn_uses_game_service_and_renders_response():
    fake_service = AsyncMock()
    fake_service.process_chat_turn.return_value = {
        "responses": [{"speaker": "analyst", "text": "你好，旅者。"}],
        "journal_events": [],
        "current_location": "camp_center",
        "environment_objects": {},
        "party_status": {"analyst": {"hp": 10}},
    }
    fake_service.get_session_state.return_value = {
        "latest_roll": {
            "intent": "PERSUASION",
            "dc": 12,
            "modifier": 2,
            "result": {"total": 15, "is_success": True},
        },
        "messages": [],
    }
    ui = _FakeUI()

    state = asyncio.run(
        main._execute_cli_turn(
            fake_service,
            ui,
            user_input="hi",
            session_id="session-1",
        )
    )

    fake_service.process_chat_turn.assert_awaited_once_with(
        user_input="hi",
        session_id="session-1",
        stream_handler=ANY,
    )
    fake_service.get_session_state.assert_awaited_once_with(session_id="session-1")
    assert state["latest_roll"]["intent"] == "PERSUASION"
    assert ui.system_info == ["⚙️ 引擎开始运转..."]
    assert ui.dice_rolls == [
        ("PERSUASION", 12, 2, {"total": 15, "is_success": True})
    ]
    assert ui.npc_streams == [("分析员", "你好，旅者。", 0.03)]


def test_render_turn_output_prints_system_response_for_command_done():
    ui = _FakeUI()
    result = {
        "responses": [],
        "journal_events": ["状态已更新"],
    }
    current_state = {
        "intent": "command_done",
        "final_response": "[SYSTEM] You used healing_potion.",
        "messages": [],
    }

    asyncio.run(main._render_turn_output(ui, result, current_state))

    assert ui.system_info == [
        "状态已更新",
        "[SYSTEM] You used healing_potion.",
    ]
    assert ui.npc_streams == []
    assert ui.dm_narrations == []
