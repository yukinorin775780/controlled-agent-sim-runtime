"""
LangGraph 节点共享工具：实体快照、默认实体加载、消息转换、物品知识库等。
"""

import copy
from typing import Any, Dict, Optional

import os
import yaml

from core.systems.inventory import get_registry

DEFAULT_EQUIPMENT: Dict[str, Any] = {"main_hand": None, "ranged": None, "armor": None}

DEFAULT_ENTITY_COORDS: Dict[str, Dict[str, int]] = {
    "player": {"x": 4, "y": 9},
    "analyst": {"x": 3, "y": 8},
    "scout": {"x": 5, "y": 8},
    "tactician": {"x": 6, "y": 8},
    "drone_1": {"x": 4, "y": 3},
    "drone_sentinel": {"x": 9, "y": 3},
    "drone_support": {"x": 8, "y": 4},
}


def _normalize_dynamic_states(raw_states: Any) -> Dict[str, Dict[str, Any]]:
    """
    规范化角色动态状态字段（如 patience/fear），统一为：
    {
      "patience": {"current_value": 15},
      "fear": {"current_value": 5}
    }
    """
    if not isinstance(raw_states, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for state_key, state_payload in raw_states.items():
        sid = str(state_key or "").strip().lower()
        if not sid:
            continue
        if isinstance(state_payload, dict):
            current_value = state_payload.get("current_value", state_payload.get("value", 0))
            try:
                current = int(current_value)
            except (TypeError, ValueError):
                current = 0
            normalized[sid] = {
                "current_value": current,
                **{k: v for k, v in state_payload.items() if k != "current_value"},
            }
            normalized[sid]["current_value"] = current
            continue
        try:
            current = int(state_payload)
        except (TypeError, ValueError):
            current = 0
        normalized[sid] = {"current_value": current}
    return normalized


def _build_item_lore(state: Any) -> str:
    """收集场上所有物品并生成 LLM 可用的物品知识库文本"""
    registry = get_registry()
    known_items = set()
    entities = state.get("entities", {})
    for ent in entities.values():
        inv = ent.get("inventory", {})
        if isinstance(inv, list):
            for item in inv:
                if isinstance(item, dict) and item.get("id"):
                    known_items.add(item["id"])
                elif isinstance(item, str):
                    known_items.add(item)
        else:
            for item_id in (inv or {}).keys():
                known_items.add(item_id)
    player_inv = state.get("player_inventory", {})
    if isinstance(player_inv, dict):
        known_items.update(player_inv.keys())
    if not known_items:
        return ""
    item_lore = (
        "\n\n[CRITICAL KNOWLEDGE: ITEM DATABASE]\n"
        "Here is the real data for the items currently in the game. "
        "Use their translated names and respect their effects/descriptions:\n"
    )
    for item_id in known_items:
        data = registry.get(item_id)
        item_lore += (
            f"- ID: {item_id} | Name: {data.get('name')} | "
            f"Desc: {data.get('description')} | Effect: {data.get('effect', 'None')}\n"
        )
    return item_lore


def _entity_snapshot(v: Dict[str, Any]) -> Dict[str, Any]:
    """
    从实体数据提取快照，保留 hp/affection/inventory 及三维状态机字段（protocol_confidence, memory_awakening）。
    确保 LangGraph 状态持久化时，各角色 Persona 状态机数值不丢失。
    """
    equipment = dict(v.get("equipment", DEFAULT_EQUIPMENT))
    legacy_weapon = equipment.pop("weapon", None)
    if legacy_weapon and not equipment.get("main_hand"):
        equipment["main_hand"] = legacy_weapon
    equipment.setdefault("main_hand", None)
    equipment.setdefault("ranged", None)
    equipment.setdefault("armor", None)
    out: Dict[str, Any] = {
        "name": v.get("name", ""),
        "faction": v.get("faction", "neutral"),
        "ability_scores": dict(v.get("ability_scores", {})),
        "speed": v.get("speed", 30),
        "hp": v.get("hp", 20),
        "max_hp": v.get("max_hp", v.get("hp", 20)),
        "ac": v.get("ac", 10),
        "status": v.get("status", "alive"),
        "active_buffs": list(v.get("active_buffs", [])),
        "status_effects": list(v.get("status_effects", [])),
        "affection": v.get("affection", 0),
        "inventory": dict(v.get("inventory", {})),
        "equipment": equipment,
        "position": v.get("position", "camp_center"),
        "x": v.get("x", 4),
        "y": v.get("y", 8),
    }
    if "protocol_confidence" in v:
        out["protocol_confidence"] = v["protocol_confidence"]
    if "memory_awakening" in v:
        out["memory_awakening"] = v["memory_awakening"]
    if "spell_slots" in v and isinstance(v.get("spell_slots"), dict):
        out["spell_slots"] = dict(v.get("spell_slots") or {})
    if "spells" in v:
        raw_spells = v.get("spells")
        if isinstance(raw_spells, list):
            out["spells"] = list(raw_spells)
        elif isinstance(raw_spells, dict):
            out["spells"] = copy.deepcopy(raw_spells)
    if "enemy_type" in v:
        out["enemy_type"] = v.get("enemy_type")
    if isinstance(v.get("dynamic_states"), dict):
        out["dynamic_states"] = copy.deepcopy(v.get("dynamic_states") or {})
    return out


def _parse_inventory(inv_raw: Any) -> Dict[str, int]:
    """
    智能解析背包：兼容「纯字符串」和「字典」两种格式。
    - 纯字符串: ["healing_potion", "mysterious_artifact"] -> 每项 count=1
    - 字典: [{id: "healing_potion", count: 2}] -> 按 id/count 解析
    """
    inv_dict: Dict[str, int] = {}
    if not isinstance(inv_raw, list):
        return inv_dict
    for item in inv_raw:
        if isinstance(item, str):
            item_id = get_registry().resolve_item_id(item)
            inv_dict[item_id] = inv_dict.get(item_id, 0) + 1
        elif isinstance(item, dict):
            iid = get_registry().resolve_item_id(item.get("id"))
            cnt = item.get("count", 1)
            if iid:
                inv_dict[iid] = inv_dict.get(iid, 0) + cnt
    return inv_dict


def _normalize_equipment(raw_equipment: Any) -> Dict[str, Any]:
    """
    将角色 YAML 中的装备列表/旧字典转换为槽位字典。
    例：["Mace", "Scale Mail"] -> {"main_hand": "mace", "ranged": None, "armor": "scale_mail"}
    """
    equipment = dict(DEFAULT_EQUIPMENT)
    registry = get_registry()

    if isinstance(raw_equipment, dict):
        for raw_slot, raw_item in raw_equipment.items():
            slot = str(raw_slot or "").strip().lower()
            if slot == "weapon":
                slot = "main_hand"
            if slot not in equipment:
                continue
            item_id = registry.resolve_item_id(raw_item)
            if item_id:
                equipment[slot] = item_id
        return equipment

    if not isinstance(raw_equipment, list):
        return equipment

    for raw_item in raw_equipment:
        raw_ref = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
        item_id = registry.resolve_item_id(raw_ref)
        if not item_id:
            continue
        item_data = registry.get_item_data(item_id)
        slot = str(item_data.get("equip_slot", "")).strip().lower()
        if slot in equipment and equipment.get(slot) is None:
            equipment[slot] = item_id
    return equipment


def load_default_entities() -> Dict[str, Dict[str, Any]]:
    """
    从 characters/*.yaml 动态加载所有角色的出厂初始状态（Data-Driven Design）。
    返回 {entity_id: {hp, active_buffs, affection, inventory, position, ...}}。
    """
    # core/graph/nodes/utils.py -> 项目根目录需向上三级
    chars_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "characters")
    entities: Dict[str, Dict[str, Any]] = {}
    for fname in sorted(os.listdir(chars_dir) if os.path.isdir(chars_dir) else []):
        if not fname.endswith(".yaml"):
            continue
        entity_id = fname[:-5]
        yaml_path = os.path.join(chars_dir, fname)
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            data = data or {}
            base = data.get("base_stats") or {}
            attrs = data.get("attributes") or {}
            combat = data.get("combat") or {}
            # 无 base_stats 且未显式声明 spawn_in_world=true 的角色卡，
            # 统一视为对话资产或 prefab，不默认注入战场实体。
            if not base and not bool(data.get("spawn_in_world", False)):
                continue
            inv_raw = data.get("inventory") or attrs.get("inventory") or []
            if isinstance(inv_raw, dict):
                inv_dict = {}
                for item_id, count in inv_raw.items():
                    key = str(item_id).strip()
                    if not key:
                        continue
                    try:
                        qty = int(count)
                    except (TypeError, ValueError):
                        qty = 0
                    if qty <= 0:
                        continue
                    inv_dict[key] = qty
            else:
                inv_dict = _parse_inventory(inv_raw)
            equipment = _normalize_equipment(
                base.get("equipment", data.get("equipment", attrs.get("equipment")))
            )
            max_hp = (
                data.get("max_hp")
                if data.get("max_hp") is not None
                else base.get("max_hp", base.get("hp", combat.get("hit_points", 20)))
            )
            hp = data.get("hp") if data.get("hp") is not None else base.get("hp", max_hp)
            coord_defaults = DEFAULT_ENTITY_COORDS.get(entity_id, {})
            entity_data: Dict[str, Any] = {
                "name": data.get("name", entity_id.replace("_", " ").title()),
                "faction": data.get("faction", base.get("faction", "neutral")),
                "ability_scores": (
                    data.get("ability_scores")
                    or attrs.get("ability_scores")
                    or {}
                ),
                "speed": base.get("speed", combat.get("speed", 30)),
                "hp": hp,
                "max_hp": max_hp,
                "ac": base.get("ac", combat.get("armor_class", 10)),
                "status": base.get("status", "alive"),
                "active_buffs": [],
                "status_effects": list(base.get("status_effects", [])),
                "affection": base.get("affection", 0),
                "inventory": inv_dict,
                "equipment": equipment,
                "position": base.get("position", "camp_center"),
                "x": base.get("x", coord_defaults.get("x", 4)),
                "y": base.get("y", coord_defaults.get("y", 8)),
            }
            dynamic_states = _normalize_dynamic_states(
                data.get("dynamic_states") or attrs.get("dynamic_states") or {}
            )
            if dynamic_states:
                entity_data["dynamic_states"] = dynamic_states
            raw_spell_slots = base.get("spell_slots", data.get("spell_slots"))
            if isinstance(raw_spell_slots, dict):
                normalized_slots: Dict[str, int] = {}
                for slot_key, slot_value in raw_spell_slots.items():
                    try:
                        normalized_slots[str(slot_key)] = max(0, int(slot_value))
                    except (TypeError, ValueError):
                        normalized_slots[str(slot_key)] = 0
                entity_data["spell_slots"] = normalized_slots
            raw_spells = data.get("spells")
            if isinstance(raw_spells, (list, dict)):
                entity_data["spells"] = copy.deepcopy(raw_spells)
            enemy_type = base.get("enemy_type", data.get("enemy_type"))
            if enemy_type:
                entity_data["enemy_type"] = str(enemy_type)
            if "protocol_confidence" in base:
                entity_data["protocol_confidence"] = base["protocol_confidence"]
            if "memory_awakening" in base:
                entity_data["memory_awakening"] = base["memory_awakening"]
            entities[entity_id] = entity_data
        except Exception:
            entities[entity_id] = {
                "name": entity_id.replace("_", " ").title(),
                "faction": "neutral",
                "ability_scores": {},
                "speed": 30,
                "hp": 20,
                "max_hp": 20,
                "ac": 10,
                "status": "alive",
                "active_buffs": [],
                "status_effects": [],
                "affection": 0,
                "inventory": {},
                "equipment": dict(DEFAULT_EQUIPMENT),
                "position": "camp_center",
                "x": DEFAULT_ENTITY_COORDS.get(entity_id, {}).get("x", 4),
                "y": DEFAULT_ENTITY_COORDS.get(entity_id, {}).get("y", 8),
            }
    return entities


# 模块加载时构建默认实体（从 YAML 驱动）
default_entities = load_default_entities()
PARTY_CORE_ENTITY_IDS = frozenset({"player", "analyst", "scout", "tactician"})


def merge_entities_with_defaults(raw_entities: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    与 input_node 一致：把 characters/*.yaml 中尚未出现在存档里的 NPC 补进 entities，
    避免多智能体路由下某轮只有部分 key 时，下游误用「缺键→0 好感」的假数据。
    """
    if not raw_entities:
        entities = copy.deepcopy(default_entities)
    else:
        entities = copy.deepcopy(raw_entities)
    if not isinstance(entities, dict):
        return copy.deepcopy(default_entities)
    for npc_id in PARTY_CORE_ENTITY_IDS:
        if npc_id not in entities and npc_id in default_entities:
            entities[npc_id] = copy.deepcopy(default_entities[npc_id])
    # 旧存档缺战斗字段时补默认值，避免 UI 与 mechanics 在旧状态上缺关键键。
    for npc_id, ent in list(entities.items()):
        if not isinstance(ent, dict):
            continue
        defaults = default_entities.get(npc_id, {})
        ent.setdefault("name", defaults.get("name", npc_id.replace("_", " ").title()))
        ent.setdefault("faction", defaults.get("faction", "neutral"))
        ent.setdefault("ability_scores", defaults.get("ability_scores", {}))
        ent.setdefault("speed", defaults.get("speed", 30))
        ent.setdefault("max_hp", defaults.get("max_hp", ent.get("hp", 20)))
        ent.setdefault("ac", defaults.get("ac", 10))
        ent.setdefault("status", defaults.get("status", "alive"))
        ent.setdefault("position", defaults.get("position", "camp_center"))
        ent.setdefault("x", defaults.get("x", DEFAULT_ENTITY_COORDS.get(npc_id, {}).get("x", 4)))
        ent.setdefault("y", defaults.get("y", DEFAULT_ENTITY_COORDS.get(npc_id, {}).get("y", 8)))
        ent.setdefault("active_buffs", [])
        ent.setdefault("status_effects", [])
        if isinstance(defaults.get("spell_slots"), dict):
            ent.setdefault("spell_slots", copy.deepcopy(defaults.get("spell_slots")))
        if "spells" in defaults:
            ent.setdefault("spells", copy.deepcopy(defaults.get("spells")))
        if "enemy_type" in defaults:
            ent.setdefault("enemy_type", defaults.get("enemy_type"))
        if isinstance(defaults.get("dynamic_states"), dict):
            ent.setdefault("dynamic_states", copy.deepcopy(defaults.get("dynamic_states")))
        ent.setdefault("inventory", {})
        equipment = ent.setdefault("equipment", dict(DEFAULT_EQUIPMENT))
        if not isinstance(equipment, dict):
            equipment = dict(DEFAULT_EQUIPMENT)
            ent["equipment"] = equipment
        legacy_weapon = equipment.pop("weapon", None)
        if legacy_weapon and not equipment.get("main_hand"):
            equipment["main_hand"] = legacy_weapon
        equipment.setdefault("main_hand", None)
        equipment.setdefault("ranged", None)
        equipment.setdefault("armor", None)
    return entities


def overlay_entity_state(state_entities: Optional[Dict[str, Any]], node_entities: Dict[str, Any]) -> Dict[str, Any]:
    """
    以进入本节点时的 state.entities 为基准（含 DM 刚写入的好感度），再叠加本节点算出的变更。
    仅覆盖 node_entities 中出现的 NPC id，避免本节点漏拷贝其它角色导致好感度被「冲掉」。
    """
    out: Dict[str, Any] = {}
    for k, v in (state_entities or {}).items():
        if isinstance(v, dict):
            out[k] = copy.deepcopy(v)
    for k, v in (node_entities or {}).items():
        if isinstance(v, dict):
            out[k] = copy.deepcopy(v)
    return out

# 世界级出厂默认（角色无关）
FACTORY_DEFAULT = {
    "player_inventory": {"healing_potion": 2},
    "turn_count": 0,
    "time_of_day": "晨曦 (Morning)",
    "flags": {},
    "combat_phase": "OUT_OF_COMBAT",
    "combat_active": False,
    "initiative_order": [],
    "current_turn_index": 0,
    "turn_resources": {},
    "recent_barks": [],
    "active_dialogue_target": None,
    "demo_cleared": False,
}


def _msg_content(m) -> str:
    """从 dict 或 LangChain message 提取 content。"""
    if isinstance(m, dict):
        return m.get("content", "")
    return getattr(m, "content", "")


def _message_to_dict(m) -> dict:
    """转为 engine 格式：{role: 'user'|'assistant', content: str}。"""
    if isinstance(m, dict):
        role = m.get("role", "user")
        role = role if role in ("user", "assistant") else "user"
        return {"role": role, "content": m.get("content", "")}
    role = getattr(m, "type", "human")
    role = "user" if role == "human" else "assistant" if role == "ai" else "user"
    return {"role": role, "content": getattr(m, "content", "")}


def first_entity_id(entities: Any) -> str:
    """
    当未设置 current_speaker 时的软回退：取 entities 的第一个 key（插入顺序）。
    空字典返回 \"unknown\".
    """
    if not isinstance(entities, dict) or not entities:
        return "unknown"
    return next(iter(entities.keys()))


def entity_display_name(entity_id: str) -> str:
    """从 characters/<id>.yaml 的 name 字段读取展示名；若无文件则格式化 id。"""
    eid = (entity_id or "").strip()
    if not eid or eid == "unknown":
        return eid or "unknown"
    try:
        from characters.loader import CharacterLoader

        data = CharacterLoader().load_character(eid)
        if isinstance(data, dict) and data.get("name"):
            return str(data["name"])
    except (FileNotFoundError, OSError, ValueError, TypeError, KeyError):
        pass
    return eid.replace("_", " ").strip().title() or "unknown"
