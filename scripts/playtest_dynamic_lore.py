"""
动态阅读游乐场：直连 lore_node，对比 INT 8 与 INT 18 的叙事输出差异。

用法：
    python scripts/playtest_dynamic_lore.py
"""

from __future__ import annotations

import os
import sys
import importlib.util
from typing import Any, Dict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import settings


def _load_lore_node():
    lore_path = os.path.join(PROJECT_ROOT, "core", "graph", "nodes", "lore.py")
    spec = importlib.util.spec_from_file_location("playtest_lore_module", lore_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 lore 模块: {lore_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lore_func = getattr(module, "lore_node", None)
    if lore_func is None:
        raise RuntimeError("lore.py 中未找到 lore_node")
    return lore_func


lore_node = _load_lore_node()


ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
    "yellow": "\033[33m",
    "green": "\033[32m",
}


def _c(text: str, color: str) -> str:
    code = ANSI.get(color, "")
    reset = ANSI["reset"] if code else ""
    return f"{code}{text}{reset}"


def _build_read_state(
    *,
    actor_id: str,
    actor_name: str,
    actor_int: int,
    persona: str,
    target_id: str = "hazard_diary",
) -> Dict[str, Any]:
    traits = [part.strip() for part in str(persona or "").split("、") if part.strip()]
    if not traits:
        traits = [str(persona or "沉默寡言").strip() or "沉默寡言"]

    return {
        "intent": "READ",
        "intent_context": {
            "action_actor": actor_id,
            "action_target": target_id,
        },
        "entities": {
            actor_id: {
                "name": actor_name,
                "ability_scores": {
                    "STR": 10,
                    "DEX": 10,
                    "CON": 10,
                    "INT": int(actor_int),
                    "WIS": 10,
                    "CHA": 10,
                },
                "attributes": {
                    "personality": {
                        "traits": traits,
                    }
                },
                "hp": 20,
                "max_hp": 20,
                "status": "alive",
            }
        },
        "environment_objects": {
            target_id: {
                "id": target_id,
                "type": "readable",
                "name": "沾满血污的日记本",
                "lore_id": "hazard_diary_1",
                "x": 15,
                "y": 3,
            }
        },
    }


def _run_case(
    *,
    actor_id: str,
    actor_name: str,
    actor_int: int,
    persona: str,
) -> None:
    state = _build_read_state(
        actor_id=actor_id,
        actor_name=actor_name,
        actor_int=actor_int,
        persona=persona,
    )
    result = lore_node(state)
    events = result.get("journal_events") or []
    final_text = str(events[-1]) if events else "（无输出）"

    title = f"[{actor_name} - INT {actor_int}]"
    print(_c(title, "magenta"))
    print(final_text)
    print()


def main() -> None:
    mode = "LLM 模式" if settings.API_KEY else "Fallback 模式（未配置 API Key）"
    print(_c("=== 🧪 动态叙事生成测试 (Lore Node) ===", "bold"))
    print(_c(f"运行模式: {mode}", "yellow"))
    print()

    _run_case(
        actor_id="scout",
        actor_name="侦察员 (Scout)",
        actor_int=18,
        persona="傲慢、毒舌、极度自恋的长期受控实验体衍体",
    )
    _run_case(
        actor_id="karlach",
        actor_name="卡菈克 (Karlach)",
        actor_int=8,
        persona="暴躁、直率、头脑简单的提夫林野蛮人",
    )

    print(_c("=== 对比结束 ===", "green"))


if __name__ == "__main__":
    main()
