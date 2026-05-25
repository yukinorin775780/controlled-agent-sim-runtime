"""
Controlled Agent Sim Runtime - V2 Main Entry Point

CLI 作为另一个前端，只负责 Rich 渲染与输入循环。
所有状态恢复、Genesis 与图编排统一委托给 GameService。
"""

import asyncio
import os
from typing import Any, Dict, List, Tuple

from core import inventory
from core.application.game_service import GameService
from core.memory.compat import get_default_memory_service
from ui.renderer import GameRenderer

DEFAULT_THREAD_ID = "sean_save_01"
SYSTEM_RESPONSE_INTENTS = {"system_wait", "command_done", "command_failed", "dev_command"}


def _speaker_display_name(speaker_id: str) -> str:
    """从 current_speaker 映射为中文显示名。"""
    names = {"analyst": "分析员", "scout": "侦察员", "dm": "Simulation Director"}
    normalized = (speaker_id or "").strip().lower()
    return names.get(normalized, (speaker_id or "未知").capitalize())


def _get_last_ai_content(messages: List[Any]) -> str:
    """从 messages 中提取最后一条 AI 消息的内容。"""
    if not messages:
        return ""
    for message in reversed(messages):
        role = getattr(message, "type", None) or (
            message.get("type") if isinstance(message, dict) else None
        )
        if role in ("ai", "assistant"):
            return getattr(message, "content", None) or (
                message.get("content", "") if isinstance(message, dict) else ""
            )
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message.get("content", "")
    return ""


def _iter_history_messages(messages: List[Any]) -> List[Tuple[str, str]]:
    """将历史消息规整为 (role, content) 便于终端展示。"""
    history: List[Tuple[str, str]] = []
    for message in messages:
        role = getattr(message, "type", None) or (
            message.get("type") if isinstance(message, dict) else None
        )
        content = getattr(message, "content", None) or (
            message.get("content", "") if isinstance(message, dict) else ""
        )
        name = getattr(message, "name", None) or (
            message.get("name") if isinstance(message, dict) else None
        )
        if not content:
            continue
        if role in ("human", "user"):
            history.append(("You", content))
        elif role in ("ai", "assistant"):
            history.append((_speaker_display_name(name or ""), content))
    return history


async def _render_turn_output(
    ui: GameRenderer,
    result: Dict[str, Any],
    current_state: Dict[str, Any],
    *,
    rendered_in_stream: bool = False,
    dice_rendered: bool = False,
) -> None:
    """按旧 CLI 风格渲染一回合的检定、日志与角色发言。"""
    latest_roll = current_state.get("latest_roll", {})
    if not dice_rendered and isinstance(latest_roll, dict) and latest_roll:
        await ui.show_dice_roll_animation(
            intent=latest_roll.get("intent", "ACTION"),
            dc=latest_roll.get("dc", 10),
            modifier=latest_roll.get("modifier", 0),
            roll_data=latest_roll.get("result", {}),
        )

    for line in result.get("journal_events") or []:
        ui.print_system_info(line)

    if rendered_in_stream:
        return

    responses = result.get("responses") or []
    if responses:
        for response in responses:
            speaker = _speaker_display_name(str(response.get("speaker", "")))
            text = str(response.get("text", ""))
            if text:
                await ui.print_npc_response_stream(speaker, text, char_delay=0.03)
        return

    ai_text = current_state.get("final_response") or _get_last_ai_content(
        current_state.get("messages") or []
    )
    if not ai_text:
        return

    intent = str(current_state.get("intent", "") or "").strip().lower()
    if intent in SYSTEM_RESPONSE_INTENTS:
        ui.print_system_info(ai_text)
        return

    if isinstance(latest_roll, dict) and latest_roll:
        ui.print_dm_narration(ai_text)
        return

    speaker = current_state.get("current_speaker", "analyst") or "analyst"
    await ui.print_npc_response_stream(
        _speaker_display_name(str(speaker)),
        str(ai_text),
        char_delay=0.03,
    )


async def _execute_cli_turn(
    game_service: GameService,
    ui: GameRenderer,
    *,
    user_input: str,
    session_id: str,
) -> Dict[str, Any]:
    """推进单回合并渲染结果，供主循环与测试复用。"""
    stream_state = {"rendered_in_stream": False, "dice_rendered": False}

    async def _handle_stream_update(node_name: str, node_state: Dict[str, Any]) -> None:
        ui.print_system_info(f"⚡ [流式追踪] 节点 `{node_name}` 执行完毕")

        if node_name == "dm_analysis":
            queue = node_state.get("speaker_queue", [])
            if queue:
                ui.print_system_info(f"👀 [Debug] DM 排出的发言队列: {queue}")
            return

        if node_name == "narration":
            dm_text = node_state.get("final_response", "")
            if dm_text:
                ui.print_dm_narration(dm_text)
                stream_state["rendered_in_stream"] = True
            return

        if node_name == "generation":
            npc_text = node_state.get("final_response", "")
            speaker_responses = node_state.get("speaker_responses", [])
            if speaker_responses:
                speaker = speaker_responses[-1][0]
            else:
                speaker = node_state.get("current_speaker", "analyst") or "analyst"
            if npc_text:
                await ui.print_npc_response_stream(
                    _speaker_display_name(str(speaker)),
                    str(npc_text),
                    char_delay=0.03,
                )
                stream_state["rendered_in_stream"] = True
            return

        if "mechanics" in node_name and "latest_roll" in node_state:
            roll_info = node_state["latest_roll"]
            await ui.show_dice_roll_animation(
                intent=roll_info.get("intent", "ACTION"),
                dc=roll_info.get("dc", 10),
                modifier=roll_info.get("modifier", 0),
                roll_data=roll_info.get("result", {}),
            )
            stream_state["dice_rendered"] = True

    ui.print_system_info("⚙️ 引擎开始运转...")
    result = await game_service.process_chat_turn(
        user_input=user_input,
        session_id=session_id,
        stream_handler=_handle_stream_update,
    )
    current_state = await game_service.get_session_state(session_id=session_id)
    await _render_turn_output(
        ui,
        result,
        current_state,
        rendered_in_stream=stream_state["rendered_in_stream"],
        dice_rendered=stream_state["dice_rendered"],
    )
    return current_state


async def main_async() -> None:
    """CLI 主循环。"""
    ui = GameRenderer()
    if not inventory.init_registry("config/items.yaml"):
        ui.print_system_info("⚠️ 警告: 物品数据库加载失败，将使用默认回退(Fallback)数据。")
    else:
        ui.print_system_info("✅ 物品数据库加载成功！")

    ui.clear_screen()
    ui.show_title("Controlled Agent Sim Runtime - V2 (LangGraph)")

    thread_id = DEFAULT_THREAD_ID
    game_service = GameService()
    current_state = await game_service.get_session_state(session_id=thread_id)

    ui.print_system_info(f"✓ 存档: {thread_id}")
    ui.print()

    history_messages = _iter_history_messages(current_state.get("messages", []))
    if history_messages:
        ui.print_rule("📜 历史对话记录", style="dim")
        for label, content in history_messages:
            ui.print(f"[dim]{label} > {content}[/dim]")
        ui.print_rule("💬 新的对话", style="info")
    else:
        ui.print_rule("💬 新的对话", style="info")

    while True:
        try:
            current_state = await game_service.get_session_state(session_id=thread_id)
            ui.show_dashboard(current_state)
            ui.print()

            user_input = ui.input_prompt()
            if not user_input or not user_input.strip():
                continue

            normalized_input = user_input.strip()
            if normalized_input.lower() in ("/quit", "quit", "exit", "退出", "q"):
                ui.print_system_info("再见。")
                break

            if normalized_input.lower() == "/reset":
                ui.print_system_info("💥 正在执行世界重置 (灭世协议)...")
                get_default_memory_service().clear_all()
                if os.path.exists("memory.db"):
                    try:
                        os.remove("memory.db")
                        ui.print_system_info("🗑️ 短期状态存档 (memory.db) 已销毁。")
                    except Exception as exc:
                        ui.print_error(f"删除存档失败: {exc}")
                ui.print_rule(
                    "世界已重置，请重新运行 `python main.py` 开启新时间线",
                    style="warning",
                )
                break

            await _execute_cli_turn(
                game_service,
                ui,
                user_input=normalized_input,
                session_id=thread_id,
            )
            ui.print()

        except KeyboardInterrupt:
            ui.print()
            ui.print_system_info("已中断。再见。")
            break
        except Exception as exc:
            ui.print_error(f"❌ 错误: {exc}")
            import traceback

            traceback.print_exc()
            ui.print()


if __name__ == "__main__":
    asyncio.run(main_async())
