#!/usr/bin/env python3
"""
Full campaign integration regression (direct mechanics pipeline).

目标：
1) Inventory + Equip
2) Stealth + Door Interact + LoS unblock
3) Surprise + Advantage + Bark hook + Surprised skip
4) Out-of-combat + Map transition

说明：
- 该脚本直接调用后端核心 mechanics 链路（不走 /api/chat），
  以降低 LLM 解析波动带来的不确定性，适合作为 Daily Build 回归。
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.systems import mechanics
from core.systems.maps import get_map_data
from core.systems.pathfinding import check_line_of_sight


RANDOM_SEED = 20260415


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_status(entity: Dict[str, Any], status_type: str) -> bool:
    needle = _normalize_id(status_type)
    for effect in _safe_list(entity.get("status_effects")):
        if isinstance(effect, dict) and _normalize_id(effect.get("type")) == needle:
            return True
    return False


def _require(condition: bool, message: str) -> None:
    if condition:
        return
    raise AssertionError(message)


def _print_logs(logs: List[str]) -> None:
    if not logs:
        print("  - 新增日志: （无）")
        return
    print("  - 新增日志:")
    for line in logs:
        print(f"    {line}")


def _apply_result_to_state(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(state)
    new_logs = [str(line) for line in _safe_list(result.get("journal_events"))]
    merged.setdefault("journal_events", [])
    merged["journal_events"] = list(_safe_list(merged.get("journal_events"))) + new_logs

    for key, value in result.items():
        if key == "journal_events":
            continue
        merged[key] = copy.deepcopy(value)
    if "raw_roll_data" in result:
        merged["latest_roll"] = copy.deepcopy(result.get("raw_roll_data"))
    return merged


def _execute_intent(state: Dict[str, Any], intent: str) -> Dict[str, Any]:
    normalized = str(intent or "").strip().upper()
    if normalized == "ATTACK":
        return mechanics.execute_attack_action(state)
    if normalized == "SHOVE":
        return mechanics.execute_shove_action(state)
    if normalized == "LOOT":
        return mechanics.execute_loot_action(state)
    if normalized == "CAST_SPELL":
        return mechanics.execute_cast_spell_action(state)
    if normalized in {"USE_ITEM", "CONSUME"}:
        return mechanics.execute_use_item(state)
    if normalized == "STEALTH":
        return mechanics.execute_stealth_action(state)
    if normalized == "EQUIP":
        return mechanics.execute_equip_action(state)
    if normalized == "UNEQUIP":
        return mechanics.execute_unequip_action(state)
    if normalized in {"MOVE", "APPROACH"}:
        return mechanics.execute_move_action(state)
    if normalized == "INTERACT":
        return mechanics.execute_interact_action(state)
    if normalized in {"END_TURN", "PASS_TURN", "WAIT_TURN"}:
        return mechanics.execute_end_turn_action(state)
    return mechanics.execute_skill_check(state)


def _run_action(
    state: Dict[str, Any],
    *,
    title: str,
    intent: str,
    actor: str = "player",
    target: str = "",
    user_input: str = "",
    item_id: Optional[str] = None,
    spell_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    payload_state = copy.deepcopy(state)
    payload_state["intent"] = intent
    payload_state["user_input"] = user_input
    intent_context = copy.deepcopy(_safe_dict(payload_state.get("intent_context")))
    intent_context["action_actor"] = actor
    if target:
        intent_context["action_target"] = target
    if item_id:
        intent_context["item_id"] = item_id
    if spell_id:
        intent_context["spell_id"] = spell_id
    payload_state["intent_context"] = intent_context

    result = _execute_intent(payload_state, intent)
    if not isinstance(result, dict):
        result = {}

    advanced = mechanics.advance_combat_after_action(payload_state, result)
    if isinstance(advanced, dict):
        result = advanced

    while True:
        entities = _safe_dict(result.get("entities")) or _safe_dict(payload_state.get("entities"))
        combat_active = bool(result.get("combat_active", payload_state.get("combat_active", False)))
        initiative_order = list(result.get("initiative_order") or payload_state.get("initiative_order") or [])
        current_turn_index = result.get("current_turn_index", payload_state.get("current_turn_index", 0))
        if not combat_active or not initiative_order:
            break
        side = mechanics._active_block_side(  # pylint: disable=protected-access
            state={**payload_state, **result},
            entities=entities,
            initiative_order=initiative_order,
            current_turn_index=current_turn_index,
        )
        if side != "hostile":
            break
        auto_advanced = mechanics.advance_combat_after_action(payload_state, result)
        if not isinstance(auto_advanced, dict) or auto_advanced == result:
            break
        result = auto_advanced

    logs = [str(line) for line in _safe_list(result.get("journal_events"))]
    if logs:
        deduped: List[str] = []
        for line in logs:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        logs = deduped
        result["journal_events"] = logs

    updated_state = _apply_result_to_state(state, result)

    print(f"\n[Action] {title}")
    _print_logs(logs)
    return updated_state, result, logs


def _seed_campaign_state() -> Dict[str, Any]:
    map_data = _safe_dict(get_map_data("training_range"))
    _require(bool(map_data), "无法加载地图 training_range。")

    player = mechanics._build_player_combatant()  # pylint: disable=protected-access
    player["x"] = 4
    player["y"] = 9
    player["position"] = "camp_center"
    player_equipment = _safe_dict(player.get("equipment"))
    player_equipment["main_hand"] = None
    player_equipment["ranged"] = None
    player_equipment["armor"] = None
    player["equipment"] = player_equipment

    entities: Dict[str, Any] = {
        "player": player,
        "drone_1": {
            "id": "drone_1",
            "name": "训练无人机",
            "faction": "hostile",
            "ability_scores": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
            "speed": 30,
            "hp": 7,
            "max_hp": 7,
            "ac": 15,
            "status": "alive",
            "inventory": {"gold_coin": 5, "scimitar": 1},
            "equipment": {"main_hand": "scimitar", "ranged": None, "armor": None},
            "position": "门后阴影",
            "x": 11,
            "y": 8,
            "active_buffs": [],
            "status_effects": [],
            "affection": 0,
        },
    }
    mechanics._inject_map_entities_from_obstacles(  # pylint: disable=protected-access
        entities=entities,
        map_data=map_data,
    )

    # 固定地面战利品：短弓，用于 LOOT + EQUIP 回归。
    entities["loot_drop_1"] = {
        "id": "loot_drop_1",
        "name": "地面战利品",
        "entity_type": "loot_drop",
        "source_name": "补给袋",
        "faction": "neutral",
        "status": "open",
        "hp": 1,
        "max_hp": 1,
        "ac": 0,
        "inventory": {"shortbow": 1},
        "equipment": {"main_hand": None, "ranged": None, "armor": None},
        "position": "camp_center",
        "x": 4,
        "y": 8,
        "active_buffs": [],
        "status_effects": [],
        "affection": 0,
    }

    return {
        "entities": entities,
        "map_data": map_data,
        "environment_objects": copy.deepcopy(map_data.get("environment_objects") or {}),
        "player_inventory": {"healing_potion": 2},
        "combat_phase": "OUT_OF_COMBAT",
        "combat_active": False,
        "initiative_order": [],
        "current_turn_index": 0,
        "turn_resources": {},
        "recent_barks": [],
        "journal_events": [],
        "current_location": str(map_data.get("name") or "训练无人机营地边缘"),
    }


def _find_door(entities: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        if str(entity.get("entity_type", "")).strip().lower() == "door":
            return str(entity_id), entity
    raise AssertionError("未找到 door 实体。")


def _drive_until_enemy_surprised_skip(state: Dict[str, Any], max_steps: int = 8) -> Tuple[Dict[str, Any], List[str]]:
    collected: List[str] = []
    for _ in range(max_steps):
        state, _, logs = _run_action(
            state,
            title="推进回合（结束我方回合）",
            intent="END_TURN",
            actor="player",
            target="party",
            user_input="结束我方回合",
        )
        collected.extend(logs)
        if any(("受惊未定" in line) or ("受惊状态" in line and "跳过本回合" in line) for line in logs):
            return state, collected
    return state, collected


def _ensure_player_can_finish_drone(state: Dict[str, Any], max_steps: int = 12) -> Dict[str, Any]:
    entities = _safe_dict(state.get("entities"))
    drone = _safe_dict(entities.get("drone_1"))
    if drone:
        drone["hp"] = min(int(drone.get("hp", 1) or 1), 1)
        drone["ac"] = 1
        entities["drone_1"] = drone
        state["entities"] = entities

    for _ in range(max_steps):
        entities = _safe_dict(state.get("entities"))
        drone = _safe_dict(entities.get("drone_1"))
        if not drone or str(drone.get("status", "")).lower() == "dead" or int(drone.get("hp", 0) or 0) <= 0:
            return state

        combat_active = bool(state.get("combat_active", False))
        if not combat_active:
            state, _, _ = _run_action(
                state,
                title="玩家远程收尾训练无人机",
                intent="ATTACK",
                actor="player",
                target="drone_1",
                user_input="玩家原地射击训练无人机",
            )
            continue

        active_block = mechanics._get_active_turn_block(  # pylint: disable=protected-access
            state=state,
            entities=entities,
        )
        if "player" in active_block:
            player_res = _safe_dict(_safe_dict(state.get("turn_resources")).get("player"))
            if int(player_res.get("action", 0) or 0) > 0:
                state, _, _ = _run_action(
                    state,
                    title="玩家远程收尾训练无人机",
                    intent="ATTACK",
                    actor="player",
                    target="drone_1",
                    user_input="玩家原地射击训练无人机",
                )
            else:
                state, _, _ = _run_action(
                    state,
                    title="玩家结束回合",
                    intent="END_TURN",
                    actor="player",
                    target="party",
                    user_input="结束我方回合",
                )
        else:
            state, _, _ = _run_action(
                state,
                title="推进到玩家可行动回合",
                intent="END_TURN",
                actor="player",
                target="party",
                user_input="结束我方回合",
            )
    return state


def main() -> int:
    random.seed(RANDOM_SEED)
    print("Starting full campaign simulation (direct mechanics pipeline)...")
    try:
        state = _seed_campaign_state()
        entities = _safe_dict(state.get("entities"))
        map_data = _safe_dict(state.get("map_data"))
        door_id, door = _find_door(entities)
        _require("transition_zone" in str(map_data.get("obstacles")), "初始地图缺少 transition_zone 配置。")
        _require(door_id == "door_oak_1", "未加载预期门实体 door_oak_1。")

        print("\n=== 第一幕：整装待发（Inventory & Equip） ===")
        state, _, _ = _run_action(
            state,
            title="玩家搜刮地面短弓",
            intent="LOOT",
            actor="player",
            target="loot_drop_1",
            user_input="玩家搜刮地面战利品",
        )
        inv_after_loot = _safe_dict(state.get("player_inventory"))
        _require(int(inv_after_loot.get("shortbow", 0) or 0) >= 1, "LOOT 后玩家背包未获得 shortbow。")

        state, _, _ = _run_action(
            state,
            title="玩家装备短弓到 ranged 槽位",
            intent="EQUIP",
            actor="player",
            target="shortbow",
            item_id="shortbow",
            user_input="玩家装备短弓",
        )
        player = _safe_dict(_safe_dict(state.get("entities")).get("player"))
        player_equipment = _safe_dict(player.get("equipment"))
        _require(player_equipment.get("ranged") == "shortbow", "EQUIP 后玩家 ranged 槽位不是 shortbow。")
        print("[Pass] 第一幕：装备系统校验成功")

        print("\n=== 第二幕：潜行与破门（Stealth & Door Interact） ===")
        state, _, _ = _run_action(
            state,
            title="玩家进入潜行",
            intent="STEALTH",
            actor="player",
            user_input="进入潜行状态",
        )
        player = _safe_dict(_safe_dict(state.get("entities")).get("player"))
        _require(_has_status(player, "hidden"), "STEALTH 后玩家未获得 hidden 状态。")

        state, _, _ = _run_action(
            state,
            title="玩家移动到门前",
            intent="MOVE",
            actor="player",
            target=door_id,
            user_input="玩家走到门前",
        )
        entities = _safe_dict(state.get("entities"))
        player = _safe_dict(entities.get("player"))
        door = _safe_dict(entities.get(door_id))
        drone = _safe_dict(entities.get("drone_1"))
        pre_los = check_line_of_sight(
            (int(player.get("x", 0)), int(player.get("y", 0))),
            (int(drone.get("x", 0)), int(drone.get("y", 0))),
            _safe_dict(state.get("map_data")),
        )
        _require(not pre_los, "关门状态下 LoS 应该被阻挡，但当前为畅通。")

        state, _, _ = _run_action(
            state,
            title="玩家开门",
            intent="INTERACT",
            actor="player",
            target=door_id,
            user_input="打开门",
        )
        entities = _safe_dict(state.get("entities"))
        player = _safe_dict(entities.get("player"))
        door = _safe_dict(entities.get(door_id))
        drone = _safe_dict(entities.get("drone_1"))
        _require(bool(door.get("is_open", False)), "INTERACT 后 door.is_open 未变为 true。")
        post_los = check_line_of_sight(
            (int(player.get("x", 0)), int(player.get("y", 0))),
            (int(drone.get("x", 0)), int(drone.get("y", 0))),
            _safe_dict(state.get("map_data")),
        )
        _require(post_los, "开门后 LoS 仍未打通。")
        _require(not bool(state.get("combat_active", False)), "开门后错误触发了战斗。")
        _require(_has_status(player, "hidden"), "开门后玩家 hidden 状态被错误清除。")
        print("[Pass] 第二幕：潜行/门交互/LoS 联动校验成功")

        print("\n=== 第三幕：受惊与嘴炮（Surprise + Advantage + Bark） ===")
        state, attack_result, attack_logs = _run_action(
            state,
            title="潜行下远程突袭训练无人机",
            intent="ATTACK",
            actor="player",
            target="drone_1",
            user_input="玩家原地射击训练无人机",
        )
        entities = _safe_dict(state.get("entities"))
        player = _safe_dict(entities.get("player"))
        drone = _safe_dict(entities.get("drone_1"))
        _require(not _has_status(player, "hidden"), "突袭后玩家 hidden 状态未清除。")
        _require(_has_status(drone, "surprised"), "突袭后训练无人机未挂载 surprised 状态。")
        _require(
            any("攻击获得优势" in line for line in attack_logs),
            "突袭攻击日志未体现 Advantage。",
        )

        raw_roll = _safe_dict(attack_result.get("raw_roll_data"))
        result_payload = _safe_dict(raw_roll.get("result"))
        is_crit = int(result_payload.get("raw_roll", 0) or 0) == 20
        is_kill = str(drone.get("status", "")).lower() == "dead" or int(drone.get("hp", 1) or 1) <= 0
        if is_crit or is_kill:
            recent_barks = _safe_list(state.get("recent_barks"))
            _require(bool(recent_barks), "发生暴击/击杀后，recent_barks 为空。")

        skip_logs = attack_logs
        if not any(("受惊未定" in line) or ("受惊状态" in line and "跳过本回合" in line) for line in skip_logs):
            state, pulled_logs = _drive_until_enemy_surprised_skip(state)
            skip_logs = skip_logs + pulled_logs
        _require(
            any(("受惊未定" in line) or ("受惊状态" in line and "跳过本回合" in line) for line in skip_logs),
            "未观察到敌方因 surprised 跳过回合的结算日志。",
        )
        print("[Pass] 第三幕：受惊/优势/动态台词触发链路校验成功")

        print("\n=== 第四幕：深渊降临（Kill -> Out of Combat -> Transition） ===")
        state = _ensure_player_can_finish_drone(state)
        entities = _safe_dict(state.get("entities"))
        drone = _safe_dict(entities.get("drone_1"))
        _require(
            str(drone.get("status", "")).lower() == "dead" or int(drone.get("hp", 1) or 1) <= 0,
            "训练无人机未被击杀，无法进入地图切换验证。",
        )
        _require(not bool(state.get("combat_active", False)), "击杀后未正确退出战斗状态。")

        state, _, _ = _run_action(
            state,
            title="玩家移动到 transition_zone (14,14)",
            intent="MOVE",
            actor="player",
            target="14,14",
            user_input="玩家前往地图边缘裂隙",
        )
        map_data = _safe_dict(state.get("map_data"))
        entities = _safe_dict(state.get("entities"))
        player = _safe_dict(entities.get("player"))
        current_location = str(state.get("current_location", "") or "")
        _require("阴暗的地下室" in current_location or "阴暗的地下室" in str(map_data.get("name", "")),
                 "未触发目标地图 service_tunnel 的重载。")
        _require(int(player.get("x", -1) or -1) == 2 and int(player.get("y", -1) or -1) == 2,
                 "地图切换后玩家坐标未落在 spawn_x/spawn_y=(2,2)。")
        _require("drone_1" not in entities, "旧地图敌人未被清空。")
        _require("door_oak_1" not in entities, "旧地图门实体未被清空。")
        print("[Pass] 第四幕：战后脱战与跨图切换校验成功")

        print("\n✅ Full campaign simulation passed.")
        return 0
    except Exception as exc:  # pragma: no cover - runtime assertion path
        print(f"\n[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
