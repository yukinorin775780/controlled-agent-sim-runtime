"""
Controlled Agent Sim Runtime — FastAPI 后端，供 Web UI 调用。
"""

import argparse
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import inventory
from core.application.game_service import (
    GameService,
    GameServiceError,
    InvalidChatRequestError,
)

BASE_DIR = Path(__file__).resolve().parent
WEB_UI_DIR = BASE_DIR / "web_ui"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时加载物品注册表，避免请求路径承担初始化职责。"""
    del app
    inventory.init_registry("config/items.yaml")
    yield


app = FastAPI(title="Controlled Agent Sim Runtime API", version="2.0", lifespan=lifespan)
game_service = GameService()


# 允许跨域请求 (为了下周的前端网页做准备)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_UI_DIR.exists():
    app.mount("/web_ui", StaticFiles(directory=str(WEB_UI_DIR), html=True), name="web_ui")


@app.exception_handler(InvalidChatRequestError)
async def handle_invalid_chat_request(
    request: Request, exc: InvalidChatRequestError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(GameServiceError)
async def handle_game_service_error(
    request: Request, exc: GameServiceError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# --- Pydantic Data Models ---
class ChatRequest(BaseModel):
    user_input: str = ""
    intent: Optional[str] = None  # 可选：系统级指令 / 挂机模式等预留意图通道
    session_id: str = "test_consume_003"  # 默认新会话，避开旧 SQLite 存档
    character: Optional[str] = None  # 可选：UI 拾取等指定角色 id（如 analyst）
    map_id: Optional[str] = None  # 可选：新会话初始化地图（如 hazard_lab）
    target: Optional[str] = None  # 可选：结构化目标 id（如 gatekeeper / heavy_oak_door_1）
    source: Optional[str] = None  # 可选：请求来源（如 interaction / ui_click）
    client_player_position: Optional[Dict[str, int]] = None  # 可选：前端本地玩家网格坐标
    player_position: Optional[List[int]] = None  # 兼容旧/简化 payload: [x, y]


class ChatResponse(BaseModel):
    responses: List[Dict[str, str]]  # 例如: [{"speaker": "scout", "text": "亲爱的..."}]
    journal_events: List[str]  # 本回合发生的新事件
    current_location: str  # 当前位置
    environment_objects: Dict[str, Any]  # 场景里的可交互物品 (如箱子、门)
    party_status: Dict[str, Any]  # 队友的血量、好感度等状态
    player_inventory: Dict[str, Any]  # 玩家背包
    combat_state: Optional[Dict[str, Any]] = None  # 回合制战斗状态
    latest_roll: Optional[Dict[str, Any]] = None
    demo_cleared: Optional[bool] = None


class ResetRequest(BaseModel):
    session_id: str = "test_consume_003"
    map_id: Optional[str] = None


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    if WEB_UI_DIR.exists():
        return RedirectResponse(url="/web_ui/")
    return RedirectResponse(url="/docs")


@app.post("/api/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    result = await game_service.process_chat_turn(
        user_input=req.user_input,
        intent=req.intent,
        session_id=req.session_id,
        character=req.character,
        map_id=req.map_id,
        target=req.target,
        source=req.source,
        client_player_position=req.client_player_position,
        player_position=req.player_position,
    )
    return ChatResponse(**result)


@app.get("/api/state")
async def state_endpoint(
    session_id: str = "test_consume_003",
    map_id: Optional[str] = None,
) -> Dict[str, Any]:
    return await game_service.get_state_snapshot(session_id=session_id, map_id=map_id)


@app.post("/api/reset", response_model=ChatResponse, response_model_exclude_none=True)
async def reset_endpoint(req: ResetRequest) -> ChatResponse:
    result = await game_service.reset_session(
        session_id=req.session_id,
        map_id=req.map_id,
    )
    return ChatResponse(**result)


def _parse_server_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled Agent Sim Runtime API server")
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser.parse_args(argv)


def _resolve_server_bind(argv: Optional[List[str]] = None) -> tuple[str, int]:
    args = _parse_server_cli_args(argv)
    env_host = str(os.getenv("CONTROLLED_AGENT_HOST") or "").strip()
    env_port_raw = str(os.getenv("CONTROLLED_AGENT_PORT") or "").strip()

    host = str(args.host or env_host or DEFAULT_HOST).strip() or DEFAULT_HOST
    port = DEFAULT_PORT
    if env_port_raw:
        try:
            port = int(env_port_raw)
        except ValueError:
            port = DEFAULT_PORT
    if args.port is not None:
        port = int(args.port)
    return host, port


def _check_port_available(host: str, port: int) -> Optional[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return str(exc)
    return None


def run_server(argv: Optional[List[str]] = None) -> None:
    import uvicorn

    host, port = _resolve_server_bind(argv)
    bind_error = _check_port_available(host, port)
    if bind_error is not None:
        print(f"❌ 端口不可用：{host}:{port} ({bind_error})")
        print("请改用其它端口，例如：")
        print("  CONTROLLED_AGENT_PORT=8010 python server.py")
        print(f"  CONTROLLED_AGENT_HOST={host} CONTROLLED_AGENT_PORT=8010 python server.py")
        print(f"  python server.py --host {host} --port 8010")
        raise SystemExit(1)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
