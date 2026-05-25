#!/usr/bin/env python3
"""
战斗系统端到端展示脚本（Showcase）。

覆盖特性：
1) 法术 AoE + 豁免检定 + 法术位消耗
2) 敌方自动回合 + A* 寻路/碰撞日志
3) 远程攻击 + DEX 加成 + LoS 分支 + 原地射击
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import requests


API_URL = "http://127.0.0.1:8010/api/chat"
SESSION_ID = "simulate-showcase"
DEFAULT_CHARACTER = "player"
PARTY_IDS = {"player", "scout", "analyst", "tactician"}

ACTION_DELAY_SEC = 2.0
MAX_AUTO_STEPS = 12


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _collect_entities(response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for source_key in ("environment_objects", "party_status", "entities"):
        payload = _safe_dict(response.get(source_key))
        for entity_id, entity_data in payload.items():
            if isinstance(entity_data, dict):
                merged[_normalize_id(entity_id)] = entity_data
    return merged


def _extract_event_lines(response: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for line in _safe_list(response.get("journal_events")):
        lines.append(str(line))

    # 兼容潜在的扩展日志字段（不是当前 ChatResponse 的稳定字段）
    for item in _safe_list(response.get("logs")):
        if isinstance(item, str):
            lines.append(item)
        elif isinstance(item, dict) and "text" in item:
            lines.append(str(item.get("text")))
    return lines


def _contains_any(response: Dict[str, Any], keywords: List[str]) -> bool:
    text = "\n".join(_extract_event_lines(response))
    return any(keyword in text for keyword in keywords)


def _is_turn_lock_response(response: Dict[str, Any]) -> bool:
    return _contains_any(response, ["动作无效", "你不能越权指挥"])


def _combat_state(response: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(response.get("combat_state"))


def _active_block(response: Dict[str, Any]) -> List[str]:
    combat = _combat_state(response)
    order = [_normalize_id(x) for x in _safe_list(combat.get("initiative_order")) if _normalize_id(x)]
    if not order:
        return []

    current_turn_index = int(combat.get("current_turn_index") or 0) % len(order)
    entities = _collect_entities(response)

    def side_of(entity_id: str) -> str:
        if entity_id in PARTY_IDS:
            return "party"
        faction = str(_safe_dict(entities.get(entity_id)).get("faction", "")).strip().lower()
        return "hostile" if faction == "hostile" else "neutral"

    first_id = order[current_turn_index]
    first_side = side_of(first_id)
    block = [first_id]
    for next_id in order[current_turn_index + 1 :]:
        if side_of(next_id) != first_side:
            break
        block.append(next_id)
    return block


def _is_combat_active(response: Dict[str, Any]) -> bool:
    return bool(_combat_state(response).get("combat_active", False))


def _is_drone_alive(response: Dict[str, Any]) -> bool:
    entities = _collect_entities(response)
    drone = _safe_dict(entities.get("drone_1"))
    if not drone:
        return False
    status = str(drone.get("status", "alive")).strip().lower()
    hp = int(drone.get("hp", 0) or 0)
    return status != "dead" and hp > 0


def _is_actor_alive(response: Dict[str, Any], actor_id: str) -> bool:
    actor = _safe_dict(_collect_entities(response).get(_normalize_id(actor_id)))
    if not actor:
        return False
    status = str(actor.get("status", "alive")).strip().lower()
    hp = int(actor.get("hp", 0) or 0)
    return status != "dead" and hp > 0


def _pick_reposition_prompt(response: Dict[str, Any]) -> str:
    entities = _collect_entities(response)
    # 优先靠近稳定存在的友方单位，避免“player 不在 entities”时移动失败。
    if _is_actor_alive(response, "analyst"):
        return "侦察员走向分析员"
    if _is_actor_alive(response, "tactician"):
        return "侦察员走向战术员"

    env = _safe_dict(response.get("environment_objects"))
    if "camp_center" in env:
        return "侦察员走向营地中央"
    if "camp_fire" in env:
        return "侦察员走向篝火"

    # 最后兜底：仍给出一个可解析的移动目标词
    return "侦察员走向训练无人机"


class ShowcaseRunner:
    def __init__(self) -> None:
        self.step = 0

    def chat(
        self,
        user_input: str = "",
        *,
        intent: Optional[str] = None,
        character: Optional[str] = DEFAULT_CHARACTER,
        title: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_input": user_input,
            "session_id": SESSION_ID,
        }
        if character is not None:
            payload["character"] = character
        if intent:
            payload["intent"] = intent

        response = requests.post(API_URL, json=payload, timeout=20)
        response.raise_for_status()
        data = _safe_dict(response.json())

        self.step += 1
        self._print_snapshot(title=title or f"Step {self.step}", request_payload=payload, response=data)
        return data

    def _print_snapshot(self, *, title: str, request_payload: Dict[str, Any], response: Dict[str, Any]) -> None:
        print("\n" + "=" * 96)
        print(f"第 {self.step} 步 · {title}")
        print("- Request:", json.dumps(request_payload, ensure_ascii=False))

        lines = _extract_event_lines(response)
        if lines:
            print("- 新增日志:")
            for line in lines:
                print("  ", line)
        else:
            print("- 新增日志: （无）")

        responses = _safe_list(response.get("responses"))
        if responses:
            print("- 对话响应:")
            for item in responses:
                speaker = _safe_dict(item).get("speaker", "npc")
                text = _safe_dict(item).get("text", "")
                print(f"  [{speaker}] {text}")

        combat = _combat_state(response)
        print("- combat_state:")
        print(json.dumps(combat, ensure_ascii=False, indent=2))

        entities = _collect_entities(response)
        for key in ("player", "analyst", "scout", "drone_1"):
            ent = _safe_dict(entities.get(key))
            if not ent:
                continue
            print(
                f"  · {key}: ({ent.get('x')}, {ent.get('y')}) "
                f"HP {ent.get('hp')}/{ent.get('max_hp', ent.get('hp'))} "
                f"status={ent.get('status')} pos={ent.get('position')}"
            )

    def ensure_combat(self, response: Dict[str, Any]) -> Dict[str, Any]:
        if _is_combat_active(response):
            return response
        next_resp = self.chat("攻击训练无人机", title="补充开战（进入战斗态）")
        time.sleep(ACTION_DELAY_SEC)
        return next_resp

    def drive_until_enemy_ai_logged(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        推动回合，直到出现敌方 AI 日志（移动/待命/攻击任一）。
        """
        current = response
        for _ in range(MAX_AUTO_STEPS):
            lines = _extract_event_lines(current)
            if any("[敌方AI]" in line for line in lines):
                return current

            if not _is_combat_active(current):
                return current

            block = _active_block(current)
            if not block:
                current = self.chat("", intent="init_sync", character=None, title="同步状态（无活跃回合组）")
                time.sleep(ACTION_DELAY_SEC)
                continue

            active_is_party = block[0] in PARTY_IDS
            if active_is_party:
                current = self.chat("结束回合", title="推进到敌方回合")
            else:
                current = self.chat("", intent="init_sync", character=None, title="同步并拉取敌方自动回合")
            time.sleep(ACTION_DELAY_SEC)
        return current

    def drive_until_actor_ready(self, response: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        """
        推动回合直到 actor_id 位于当前我方连携组中。
        """
        current = response
        actor_id = _normalize_id(actor_id)
        for _ in range(MAX_AUTO_STEPS):
            if not _is_combat_active(current):
                return current
            block = _active_block(current)
            if block and block[0] in PARTY_IDS and actor_id in block:
                return current

            if block and block[0] in PARTY_IDS:
                current = self.chat("结束回合", title=f"当前非 {actor_id} 连携组，推进回合")
            else:
                current = self.chat("", intent="init_sync", character=None, title="敌方回合同步")
            time.sleep(ACTION_DELAY_SEC)
        return current


def _print_expectation_check(title: str, response: Dict[str, Any], keywords: List[str]) -> None:
    lines = "\n".join(_extract_event_lines(response))
    ok = all(keyword in lines for keyword in keywords)
    mark = "✅" if ok else "⚠️"
    print(f"{mark} {title}: {'通过' if ok else '未完全满足'}")
    if not ok:
        print(f"   期望关键字: {keywords}")


def main() -> None:
    print("Starting combat showcase simulation...")
    runner = ShowcaseRunner()

    try:
        # 初始化并重置到默认地图（training_range）
        runner.chat("", intent="init_sync", character=None, title="初始化同步")
        time.sleep(ACTION_DELAY_SEC)
        runner.chat("/reset", title="重置世界（加载 training_range）")
        time.sleep(ACTION_DELAY_SEC)
        state = runner.chat("", intent="init_sync", character=None, title="重置后同步")
        time.sleep(ACTION_DELAY_SEC)
    except requests.RequestException as exc:
        print("❌ 无法连接到后端 API，请确认服务已启动：", exc)
        return

    print("\n--- 第一幕：魔法轰炸（Spell + AoE + Saving Throw） ---")
    spell_attempts = [
        ("Analyst移动到训练无人机旁边，施放雷鸣波", "分析员施放雷鸣波（复合指令）"),
        ("分析员施放雷鸣波攻击训练无人机", "法术降级重试（中文标准口令）"),
        ("analyst cast thunderwave on drone_1", "法术降级重试（英文+ID口令）"),
    ]
    spell_ok = False
    for command, title in spell_attempts:
        state = runner.chat(command, title=title)
        time.sleep(ACTION_DELAY_SEC)
        if _contains_any(state, ["施放了 雷鸣波", "雷鸣波"]) and _contains_any(state, ["豁免", "消耗1环法术位"]):
            spell_ok = True
            break

    _print_expectation_check("法术日志", state, ["雷鸣波"])
    _print_expectation_check("豁免日志", state, ["豁免"])

    state = runner.ensure_combat(state)
    time.sleep(ACTION_DELAY_SEC)

    if not _is_drone_alive(state):
        print("\n⚠️ 第一幕后训练无人机已倒下，重置场景以保证第二幕可演示。")
        runner.chat("/reset", title="重置世界（第二幕重开）")
        time.sleep(ACTION_DELAY_SEC)
        state = runner.chat("", intent="init_sync", character=None, title="重置后同步")
        time.sleep(ACTION_DELAY_SEC)
        state = runner.chat("攻击训练无人机", title="重新开战（第二幕）")
        time.sleep(ACTION_DELAY_SEC)

    print("\n--- 第二幕：智能寻路（Enemy AI + A* + Collision） ---")
    state = runner.drive_until_enemy_ai_logged(state)
    _print_expectation_check("敌方 AI 日志", state, ["[敌方AI]"])
    time.sleep(ACTION_DELAY_SEC)

    if not _is_drone_alive(state):
        print("\n⚠️ 训练无人机已提前倒下，重置场景以继续第三幕远程演示。")
        runner.chat("/reset", title="重置世界（第三幕重开）")
        time.sleep(ACTION_DELAY_SEC)
        state = runner.chat("", intent="init_sync", character=None, title="重置后同步")
        time.sleep(ACTION_DELAY_SEC)
        state = runner.chat("攻击训练无人机", title="重新开战")
        time.sleep(ACTION_DELAY_SEC)

    print("\n--- 第三幕：远程狙击（Ranged + DEX + LoS） ---")
    # 第三幕独立重置，避免继承前两幕的回合资源/战损残局，确保演示稳定。
    state = runner.chat("/reset", title="第三幕独立重置（清理残局）")
    time.sleep(ACTION_DELAY_SEC)
    state = runner.chat("", intent="init_sync", character=None, title="第三幕重置后同步")
    time.sleep(ACTION_DELAY_SEC)

    # 先打一枪观察 LoS；若被遮挡，再换位后二次射击拿到 DEX 计算日志。
    first_shot = runner.chat("侦察员原地射击训练无人机", title="侦察员远程射击（首发）")
    time.sleep(ACTION_DELAY_SEC)
    _print_expectation_check("远程武器日志", first_shot, ["短弓"])

    first_lines = "\n".join(_extract_event_lines(first_shot))
    if "视线范围内" in first_lines:
        print("✅ LoS 拦截日志：通过（障碍物成功阻挡远程攻击）。")
        if not _is_actor_alive(first_shot, "scout"):
            # 极端情况下被反打阵亡，重新开始第三幕。
            state = runner.chat("/reset", title="侦察员倒地，第三幕重置重试")
            time.sleep(ACTION_DELAY_SEC)
            state = runner.chat("", intent="init_sync", character=None, title="重置后同步")
            time.sleep(ACTION_DELAY_SEC)
        else:
            state = first_shot

        # 先推进到侦察员可行动回合，避免回合锁驳回。
        state = runner.drive_until_actor_ready(state, "scout")
        time.sleep(ACTION_DELAY_SEC)

        move_prompt = _pick_reposition_prompt(state)
        state = runner.chat(move_prompt, title=f"LoS 被挡后换位（{move_prompt}）")
        time.sleep(ACTION_DELAY_SEC)
        if _is_turn_lock_response(state):
            state = runner.drive_until_actor_ready(state, "scout")
            time.sleep(ACTION_DELAY_SEC)
            move_prompt = _pick_reposition_prompt(state)
            state = runner.chat(move_prompt, title=f"LoS 被挡后换位（回合锁重试：{move_prompt}）")
            time.sleep(ACTION_DELAY_SEC)

        state = runner.drive_until_actor_ready(state, "scout")
        time.sleep(ACTION_DELAY_SEC)
        second_shot = runner.chat("侦察员原地射击训练无人机", title="换位后二次远程射击（验证 DEX）")
        time.sleep(ACTION_DELAY_SEC)
        if _is_turn_lock_response(second_shot):
            state = runner.drive_until_actor_ready(second_shot, "scout")
            time.sleep(ACTION_DELAY_SEC)
            second_shot = runner.chat("侦察员原地射击训练无人机", title="换位后二次远程射击（回合锁重试）")
            time.sleep(ACTION_DELAY_SEC)
        shot_lines = "\n".join(_extract_event_lines(second_shot))
    else:
        print("✅ LoS 未阻挡：本次直接进入远程命中判定。")
        second_shot = first_shot
        shot_lines = first_lines

    if "[战术走位]" in shot_lines:
        print("⚠️ 本轮出现走位日志：目标可能在射程外或站位变化。")
    else:
        print("✅ 原地射击：未检测到走位日志。")

    _print_expectation_check("敏捷加成日志", second_shot, ["敏捷"])

    print("\nShowcase completed.")


if __name__ == "__main__":
    main()
