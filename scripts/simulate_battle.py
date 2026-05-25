#!/usr/bin/env python3
"""
Simulate a full battle loop against the drone via /api/chat.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


API_URL = "http://127.0.0.1:8010/api/chat"
SESSION_ID = "simulate-battle"
CHARACTER = "player"
POLL_SLEEP_SEC = 0.05
MAX_STEPS = 50


def _post_chat(user_input: str, intent: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "user_input": user_input,
        "session_id": SESSION_ID,
        "character": CHARACTER,
    }
    if intent:
        payload["intent"] = intent
    resp = requests.post(API_URL, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _extract_logs(response: Dict[str, Any]) -> Tuple[List[str], bool]:
    logs = response.get("logs")
    if isinstance(logs, list):
        return [str(line) for line in logs], True
    journal = response.get("journal_events")
    if isinstance(journal, list):
        return [str(line) for line in journal], False
    return [], False


def _get_active_block(order: List[str], current_turn_index: int, party_ids: set[str], entities: Dict[str, Any]) -> List[str]:
    if not order:
        return []
    idx = current_turn_index % len(order)
    first_id = order[idx]
    first_entity = entities.get(first_id, {})
    first_faction = str(first_entity.get("faction", "")).strip().lower()
    if first_id in party_ids:
        side_key = "party"
    elif first_faction == "hostile":
        side_key = "hostile"
    else:
        side_key = "neutral"

    block = [first_id]
    for next_id in order[idx + 1 :]:
        next_entity = entities.get(next_id, {})
        next_faction = str(next_entity.get("faction", "")).strip().lower()
        if next_id in party_ids:
            next_side = "party"
        elif next_faction == "hostile":
            next_side = "hostile"
        else:
            next_side = "neutral"
        if next_side != side_key:
            break
        block.append(next_id)
    return block


def _extract_damage_line(journal_events: List[str]) -> Optional[str]:
    for line in reversed(journal_events):
        if "造成" in line and "伤害" in line:
            return line
    return None


def _summarize_turn(step: int, actor: str, journal_events: List[str]) -> str:
    for line in reversed(journal_events):
        if "动作无效" in line or "动作资源不足" in line:
            return f"第 {step} 步：{actor} 行动被拒绝。{line}"
    return f"第 {step} 步：{actor} 行动完成。"


def _is_drone_dead(entities: Dict[str, Any]) -> bool:
    drone = entities.get("drone_1", {})
    status = str(drone.get("status", "")).strip().lower()
    return status == "dead" or int(drone.get("hp", 1) or 1) <= 0


def _is_party_defeated(entities: Dict[str, Any]) -> bool:
    party_ids = ("player", "scout", "analyst", "tactician")
    for actor_id in party_ids:
        actor = entities.get(actor_id, {})
        if not isinstance(actor, dict):
            continue
        status = str(actor.get("status", "")).strip().lower()
        hp = int(actor.get("hp", 0) or 0)
        if status != "dead" and hp > 0:
            return False
    return True


def main() -> None:
    print("Starting battle simulation...")
    try:
        result = _post_chat("攻击训练无人机")
    except Exception as exc:  # pragma: no cover - runtime only
        print("Failed to reach API. Is the server running?")
        print(exc)
        return

    steps = 1
    last_log_index = 0
    last_turn_index: Optional[int] = None
    stagnant_turns = 0
    while steps <= MAX_STEPS:
        combat_state = result.get("combat_state") or {}
        combat_active = bool(combat_state.get("combat_active", False))
        entities = result.get("party_status") or result.get("entities") or {}
        environment_objects = result.get("environment_objects") or {}
        all_entities = {}
        all_entities.update(environment_objects if isinstance(environment_objects, dict) else {})
        all_entities.update(entities if isinstance(entities, dict) else {})
        all_entities.update(result.get("entities") or {})

        logs, is_cumulative = _extract_logs(result)
        if not is_cumulative:
            last_log_index = 0
        if len(logs) < last_log_index:
            last_log_index = 0
        new_logs = logs[last_log_index:]
        for line in new_logs:
            print(line)
        last_log_index = len(logs)

        if not combat_active or any("训练无人机" in line and "倒下" in line for line in logs):
            if _is_drone_dead(all_entities):
                print("✅ 自动化战斗模拟成功：训练无人机已被击杀！")
            elif _is_party_defeated(all_entities):
                print("❌ 自动化战斗模拟结束：我方全灭。")
            else:
                print("⚠️ 战斗结束，但未检测到训练无人机死亡。")
            return

        order = list(combat_state.get("initiative_order") or [])
        current_turn_index = int(combat_state.get("current_turn_index") or 0)
        turn_resources = combat_state.get("turn_resources") or {}
        party_ids = {"player", "scout", "analyst", "tactician"}
        active_block = _get_active_block(order, current_turn_index, party_ids, all_entities)
        if not active_block:
            print("⚠️ 无法解析当前行动组，停止模拟。")
            return

        active_side = "party" if active_block[0] in party_ids else "enemy"
        if active_side == "enemy":
            result = _post_chat("", intent="init_sync")
            steps += 1
            time.sleep(POLL_SLEEP_SEC)
            continue

        action_available = any(
            int(turn_resources.get(actor_id, {}).get("action", 0) or 0) > 0
            for actor_id in active_block
        )
        if last_turn_index == current_turn_index and not action_available:
            stagnant_turns += 1
        else:
            stagnant_turns = 0
        last_turn_index = current_turn_index
        if stagnant_turns >= 3:
            print("⚠️ 检测到回合死锁，强制退出。当前 combat_state:")
            print(json.dumps(combat_state, ensure_ascii=False, indent=2))
            return

        # Party block turn: pick the first actor in block who still has action.
        chosen_actor = next(
            (
                actor_id
                for actor_id in active_block
                if int(turn_resources.get(actor_id, {}).get("action", 0) or 0) > 0
            ),
            active_block[0],
        )
        chosen_resources = turn_resources.get(chosen_actor, {})
        if int(chosen_resources.get("action", 0) or 0) > 0:
            actor_name = all_entities.get(chosen_actor, {}).get("name", chosen_actor)
            result = _post_chat(f"{actor_name} 攻击训练无人机")
            print(_summarize_turn(steps, actor_name, result.get("journal_events", [])))
        else:
            result = _post_chat("结束回合")
            print(f"第 {steps} 步：结束我方连携回合。")

        if _is_drone_dead(all_entities):
            print("✅ 自动化战斗模拟成功：训练无人机已被击杀！")
            return

        steps += 1
        time.sleep(POLL_SLEEP_SEC)

    print("⚠️ 达到最大步数，战斗未结束。")


if __name__ == "__main__":
    main()
