"""
物理引擎：物品流转与生命值变动的底层结算。
V3 架构：从 dm_node 抽离，单一职责。
"""

import logging
from typing import Any, Dict, List

from core.systems.inventory import get_registry

logger = logging.getLogger(__name__)

# 开发者测试：为 True 时 `core.systems.dice.roll_d20` 固定自然 20（大成功），跳过随机掷骰
DEBUG_ALWAYS_PASS_CHECKS = True


def _is_consumable_item(item_id: str, item_data: Dict[str, Any]) -> bool:
    if item_data.get("equip_slot"):
        return False
    if item_data.get("is_consumable") is True:
        return True
    return str(item_data.get("type", "")).strip().lower() == "consumable"


def apply_physics(
    current_entities: dict,
    player_inventory: dict,
    item_transfers: list,
    hp_changes: list,
) -> List[str]:
    """
    处理实体的物理结算，包括物品转移、消耗、环境掉落以及生命值变动。
    直接修改传入的 current_entities 与 player_inventory 字典。
    返回产生的 journal_events 列表。
    """
    journal_events: List[str] = []
    registry = get_registry()

    # 1. 处理物品转移 (Item Transfers)
    for transfer in item_transfers:
        if not isinstance(transfer, dict):
            continue
        src = transfer.get("from", "player")
        dst = transfer.get("to")
        item_id = transfer.get("item_id")
        count = int(transfer.get("count", 1))

        if not item_id or count <= 0:
            continue

        # --- 世界掉落 (World Drop) 逻辑 ---
        if src == "world" and dst and count > 0:
            item_name = registry.get_name(item_id)
            if dst == "player":
                player_inventory[item_id] = player_inventory.get(item_id, 0) + count
                journal_events.append(f"✨ [环境发现] {dst} 获得了 {count}x {item_name}")
            elif dst in current_entities:
                dst_inv = current_entities[dst].setdefault("inventory", {})
                if not isinstance(dst_inv, dict):
                    dst_inv = {}
                    current_entities[dst]["inventory"] = dst_inv
                dst_inv[item_id] = dst_inv.get(item_id, 0) + count
                journal_events.append(f"✨ [环境发现] {dst} 获得了 {count}x {item_name}")
            continue
        # ------------------------------------------

        # 获取源背包（player 用 player_inventory，NPC 用 entities）
        src_inv: Dict[str, int] = {}
        if src == "player":
            src_inv = player_inventory
        elif src in current_entities:
            inv = current_entities[src].setdefault("inventory", {})
            if not isinstance(inv, dict):
                inv = {}
                current_entities[src]["inventory"] = inv
            src_inv = inv
        else:
            continue

        item_data = registry.get_item_data(item_id)
        item_name = registry.get_name(item_id)
        # 【修复物理黑洞】先校验 dst 是否合法，不合法则报错并 continue，绝不扣除源物品
        dst_valid = dst in ("consumed", "player") or (dst and dst in current_entities)
        if not dst_valid:
            journal_events.append(f"❌ [动作失败] 无效的目标: {dst}")
            continue
        if dst == "consumed" and not _is_consumable_item(item_id, item_data):
            logger.warning("拦截了 LLM 试图消耗武器的行为: %s", item_id)
            journal_events.append(f"❌ [物品使用] {item_name} 不是可消耗物品，不能被消耗。")
            continue

        has_enough = src_inv.get(item_id, 0) >= count
        if not has_enough:
            journal_events.append(f"❌ [动作失败] {src} 并没有足够的 {item_name}！")
            continue

        # 1. 扣除来源物品（仅在校验通过后执行）
        src_inv[item_id] = src_inv.get(item_id, 0) - count
        if src_inv[item_id] <= 0:
            del src_inv[item_id]

        # 2. 增加目标物品（若不是被消耗）
        if dst == "consumed":
            journal_events.append(f"💥 [物品消耗] {src} 使用了 {count}x {item_name}")
        elif dst == "player":
            player_inventory[item_id] = player_inventory.get(item_id, 0) + count
            journal_events.append(f"📦 [物品流转] {src} 将 {count}x {item_name} 交给了 {dst}")
        elif dst in current_entities:
            dst_inv = current_entities[dst].setdefault("inventory", {})
            if not isinstance(dst_inv, dict):
                dst_inv = {}
                current_entities[dst]["inventory"] = dst_inv
            dst_inv[item_id] = dst_inv.get(item_id, 0) + count
            journal_events.append(f"📦 [物品流转] {src} 将 {count}x {item_name} 交给了 {dst}")

    # 2. 处理生命值变动 (HP Changes)
    for change in hp_changes:
        if not isinstance(change, dict):
            continue
        target = change.get("target")
        amount = int(change.get("amount", 0))
        if not target or amount == 0:
            continue
        # 支持 player：若不在 entities 中则创建
        if target == "player" and target not in current_entities:
            current_entities["player"] = {
                "hp": 20,
                "max_hp": 20,
                "affection": 0,
                "inventory": {},
                "active_buffs": [],
                "position": "camp_center",
            }
        if target not in current_entities:
            continue
        ent = current_entities[target]
        current_hp = ent.get("hp", 20)
        base_stats = ent.get("base_stats") or {}
        max_hp = ent.get("max_hp", base_stats.get("hp", 20))
        new_hp = max(0, min(current_hp + amount, max_hp))
        ent["hp"] = new_hp
        action_word = "恢复了" if amount > 0 else "失去了"
        color_icon = "💚" if amount > 0 else "🩸"
        journal_events.append(
            f"{color_icon} [状态变动] {target} {action_word} {abs(amount)} 点 HP (当前: {new_hp}/{max_hp})"
        )

    return journal_events


def apply_movement(current_entities: dict, actor_id: str, target_location: str) -> List[str]:
    """
    语义地标移动：将 NPC 的 position 更新为语义 waypoint id（如 camp_fire），非绝对坐标。
    直接修改 current_entities[actor_id]。
    """
    if not actor_id or not isinstance(current_entities, dict):
        return []
    if actor_id not in current_entities or not isinstance(current_entities[actor_id], dict):
        return [f"❌ [移动失败] 未知角色: {actor_id}"]
    tid = (target_location or "").strip()
    if not tid:
        return [f"❌ [移动失败] {actor_id} 未指定目标地点（target_id）。"]
    ent = current_entities[actor_id]
    ent["position"] = tid
    return [f"🏃 [物理移动] {actor_id.capitalize()} 移动到了 {tid}。"]


def execute_loot(
    entities: Dict[str, Any],
    environment_objects: Dict[str, Any],
    character_id: str,
    target_obj_id: str,
) -> str:
    """
    将环境物体 inventory 内全部物品转移到指定角色背包，并清空该物体 inventory。
    直接修改传入的 entities 与 environment_objects。
    返回一条可写入 journal 的日志字符串。
    """
    if not character_id or character_id not in entities:
        return f"❌ [系统] 未找到角色: {character_id}"
    if not target_obj_id or target_obj_id not in environment_objects:
        return f"❌ [系统] 未找到环境物体: {target_obj_id}"

    obj = environment_objects[target_obj_id]
    if not isinstance(obj, dict):
        return f"❌ [系统] 无效的环境物体数据: {target_obj_id}"

    inv = obj.get("inventory")
    if not isinstance(inv, dict):
        inv = {}

    ent = entities[character_id]
    dst = ent.setdefault("inventory", {})
    if not isinstance(dst, dict):
        dst = {}
        entities[character_id]["inventory"] = dst

    for item_id, count in list(inv.items()):
        if not item_id:
            continue
        try:
            c = int(count)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        dst[item_id] = dst.get(item_id, 0) + c

    obj["inventory"] = {}
    return f"🎒 [系统] {character_id} 搜刮了 {target_obj_id} 的所有物品。"


def apply_environment_interaction(env_objects: dict, target_id: str, action_detail: str, actor_id: str) -> List[str]:
    """
    处理角色与环境物体（如宝箱、门）的物理交互。
    """
    events: List[str] = []
    if target_id not in env_objects:
        events.append(f"📦 [{actor_id}] 试图操作 {target_id}，但这里似乎没有这个东西。")
        return events

    obj = env_objects[target_id]
    obj_name = obj.get("name", target_id)
    current_status = obj.get("status", "unknown")

    if action_detail in ["unlock", "open"]:
        if current_status == "locked":
            obj["status"] = "opened"
            events.append(f"🔓 [{actor_id}] 灵巧地拨弄着锁芯，咔哒一声，【{obj_name}】被打开了！")
        elif current_status == "opened":
            events.append(f"📦 [{actor_id}] 检查了【{obj_name}】，但它已经是打开的状态了。")
        else:
            obj["status"] = "opened"
            events.append(f"📦 [{actor_id}] 打开了【{obj_name}】。")
    elif action_detail in ["close"]:
        obj["status"] = "closed"
        events.append(f"📦 [{actor_id}] 关上了【{obj_name}】。")
    elif action_detail in ["attack", "destroy", "kick"]:
        obj["status"] = "destroyed"
        events.append(f"💥 [{actor_id}] 粗暴地破坏了【{obj_name}】！碎片散落一地。")
    else:
        events.append(f"📦 [{actor_id}] 对【{obj_name}】执行了动作：{action_detail}。")

    return events
