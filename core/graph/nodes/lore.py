"""
Lore 节点：处理 READ 意图，读取环境中的可阅读文本并按阅读者属性动态解读。
"""

from __future__ import annotations

import copy
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

import yaml
from openai import OpenAI

from config import settings
from core.events.models import DomainEvent, event_to_dict
from core.systems import dice as dice_module
from core.utils.text_processor import parse_llm_json

if TYPE_CHECKING:
    from core.graph.graph_state import GameState
else:
    GameState = Dict[str, Any]

logger = logging.getLogger(__name__)
LLM_TIMEOUT_SECONDS = 4.5

_LORE_CACHE: Dict[str, Dict[str, Any]] = {}
DIARY_LORE_IDS = frozenset({"hazard_diary_1"})
LORE_TIMEOUT_FALLBACK = {
    "narrator_text": "日记上的字迹被血污彻底覆盖...",
    "character_monologue": "（烦躁地啧了一声）完全看不清写了什么，真是浪费时间。",
}
DIARY_DECODE_DC = 14
DIARY_AUTO_SUCCESS_INT = 14
DIARY_AUTO_FAILURE_INT = 10
READABLE_TARGET_ALIASES = {
    "chemical_notes": "chemical_notes",
    "药剂笔记": "chemical_notes",
    "化学残页": "chemical_notes",
    "化学笔记": "chemical_notes",
    "iron_key_sketch": "iron_key_sketch",
    "铁钥匙草图": "iron_key_sketch",
    "重铁钥匙草图": "iron_key_sketch",
    "钥匙草图": "iron_key_sketch",
}


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_lore_db(force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    global _LORE_CACHE
    if _LORE_CACHE and not force_reload:
        return _LORE_CACHE
    lore_path = os.path.join(_project_root(), "data", "lore.yaml")
    if not os.path.exists(lore_path):
        _LORE_CACHE = {}
        return _LORE_CACHE
    try:
        with open(lore_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("Failed to load lore yaml: %s", lore_path)
        _LORE_CACHE = {}
        return _LORE_CACHE
    if not isinstance(raw_data, dict):
        _LORE_CACHE = {}
        return _LORE_CACHE
    normalized: Dict[str, Dict[str, Any]] = {}
    for lore_id_raw, lore_data in raw_data.items():
        lore_id = _normalize_id(lore_id_raw)
        if not lore_id or not isinstance(lore_data, dict):
            continue
        normalized[lore_id] = lore_data
    _LORE_CACHE = normalized
    return _LORE_CACHE


def _display_entity_name(entity_id: str, entity: Dict[str, Any]) -> str:
    name = str(entity.get("name") or "").strip()
    if name:
        return name
    if entity_id == "player":
        return "玩家"
    return entity_id.replace("_", " ").strip().title() or "未知角色"


def _resolve_readable_target(
    *,
    environment_objects: Dict[str, Dict[str, Any]],
    target_id: str,
) -> tuple[str, Optional[Dict[str, Any]]]:
    normalized_target = _normalize_id(target_id)
    if not normalized_target:
        return "", None
    normalized_target = READABLE_TARGET_ALIASES.get(normalized_target, normalized_target)

    if normalized_target in environment_objects:
        target_obj = environment_objects.get(normalized_target)
        if isinstance(target_obj, dict):
            return normalized_target, target_obj

    for object_id, object_data in environment_objects.items():
        if not isinstance(object_data, dict):
            continue
        object_name = _normalize_id(object_data.get("name"))
        normalized_object_id = _normalize_id(object_id)
        alias_ids = object_data.get("alias_ids")
        if not isinstance(alias_ids, list):
            alias_ids = [object_data.get("alias_id")]
        normalized_aliases = {_normalize_id(alias) for alias in alias_ids if _normalize_id(alias)}
        if (
            normalized_target in object_name
            or object_name in normalized_target
            or normalized_target in normalized_object_id
            or normalized_object_id in normalized_target
            or normalized_target in normalized_aliases
        ):
            return normalized_object_id, object_data
    return "", None


def _extract_actor_int(entity: Dict[str, Any]) -> int:
    ability_scores = entity.get("ability_scores")
    if not isinstance(ability_scores, dict):
        return 10
    for key, value in ability_scores.items():
        if str(key).strip().upper() != "INT":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return 10
    return 10


def _extract_actor_personality(entity: Dict[str, Any]) -> str:
    attributes = entity.get("attributes")
    if not isinstance(attributes, dict):
        return ""
    personality = attributes.get("personality")
    if not isinstance(personality, dict):
        return ""
    traits = personality.get("traits")
    if not isinstance(traits, list):
        return ""
    normalized_traits = [str(trait).strip() for trait in traits if str(trait).strip()]
    return "；".join(normalized_traits[:3])


def _extract_actor_skill_bonus(entity: Dict[str, Any], *, skill: str) -> int:
    normalized_skill = _normalize_id(skill)
    skills = entity.get("skills")
    if isinstance(skills, dict):
        for key, value in skills.items():
            if _normalize_id(key) != normalized_skill:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                break
    if normalized_skill == "arcana":
        return 2
    if normalized_skill == "investigation":
        return 1
    return 0


def _ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def _normalize_diary_skill(raw_skill: Any) -> str:
    normalized = _normalize_id(raw_skill)
    if normalized in {"arcana", "奥术"}:
        return "arcana"
    if normalized in {"investigation", "调查", "investigate"}:
        return "investigation"
    if normalized in {"int", "intelligence", "智力"}:
        return "intelligence"
    return ""


def _detect_diary_skill(*, intent_context: Dict[str, Any], user_input: str) -> str:
    mapped = _normalize_diary_skill(
        intent_context.get("skill")
        or intent_context.get("check_skill")
        or intent_context.get("check")
    )
    if mapped:
        return mapped
    normalized_input = str(user_input or "").strip().lower()
    if "arcana" in normalized_input or "奥术" in user_input:
        return "arcana"
    if "investigation" in normalized_input or "调查" in user_input:
        return "investigation"
    if "intelligence" in normalized_input or "智力" in user_input:
        return "intelligence"
    return "intelligence"


def _resolve_diary_check(
    *,
    actor: Dict[str, Any],
    int_score: int,
    skill: str,
    context_gathered: bool = False,
) -> Dict[str, Any]:
    normalized_skill = _normalize_diary_skill(skill) or "intelligence"
    ability_bonus = _ability_modifier(int_score)
    skill_bonus = _extract_actor_skill_bonus(actor, skill=normalized_skill)
    modifier = ability_bonus + skill_bonus

    if context_gathered:
        context_bonus = 10
        total = 10 + modifier + context_bonus
        return {
            "skill": normalized_skill,
            "dc": DIARY_DECODE_DC,
            "modifier": modifier + context_bonus,
            "result": {
                "raw_roll": 10,
                "total": total,
                "is_success": True,
                "result_type": "CONTEXT_SUCCESS",
                "context_bonus": context_bonus,
            },
            "is_success": True,
        }

    if int_score >= DIARY_AUTO_SUCCESS_INT:
        return {
            "skill": normalized_skill,
            "dc": DIARY_DECODE_DC,
            "modifier": modifier,
            "result": {
                "raw_roll": 20,
                "total": 20 + modifier,
                "is_success": True,
                "result_type": "AUTO_SUCCESS_INT",
            },
            "is_success": True,
        }
    if int_score < DIARY_AUTO_FAILURE_INT:
        return {
            "skill": normalized_skill,
            "dc": DIARY_DECODE_DC,
            "modifier": modifier,
            "result": {
                "raw_roll": 1,
                "total": 1 + modifier,
                "is_success": False,
                "result_type": "AUTO_FAILURE_INT",
            },
            "is_success": False,
        }

    raw_roll = int(dice_module.random.randint(1, 20))
    total = raw_roll + modifier
    if raw_roll == 20:
        is_success = True
        result_type = "CRITICAL_SUCCESS"
    elif raw_roll == 1:
        is_success = False
        result_type = "CRITICAL_FAILURE"
    else:
        is_success = total >= DIARY_DECODE_DC
        result_type = "SUCCESS" if is_success else "FAILURE"
    return {
        "skill": normalized_skill,
        "dc": DIARY_DECODE_DC,
        "modifier": modifier,
        "result": {
            "raw_roll": raw_roll,
            "total": total,
            "is_success": bool(is_success),
            "result_type": result_type,
        },
        "is_success": bool(is_success),
    }


def _build_memory_event(
    *,
    event_id: str,
    actor_id: str,
    turn_index: int,
    visibility: str,
    scope: str,
    text: str,
    location_id: str,
    participants: list[str],
) -> Dict[str, Any]:
    return event_to_dict(
        DomainEvent(
            event_id=event_id,
            event_type="actor_memory_update_requested",
            actor_id=actor_id,
            turn_index=turn_index,
            visibility=visibility,
            payload={
                "scope": scope,
                "memory_type": "lore",
                "text": text,
                "participants": participants,
                "location_id": location_id,
            },
        )
    )


def _build_diary_resolution(
    *,
    state: GameState,
    actor_id: str,
    actor: Dict[str, Any],
    int_score: int,
) -> Dict[str, Any]:
    intent_context = state.get("intent_context") or {}
    user_input = str(state.get("user_input") or "")
    skill = _detect_diary_skill(
        intent_context=intent_context if isinstance(intent_context, dict) else {},
        user_input=user_input,
    )
    flags = dict(state.get("flags") or {})
    context_gathered = bool(flags.get("act3_diary_context_gathered", False))
    check = _resolve_diary_check(
        actor=actor,
        int_score=int_score,
        skill=skill,
        context_gathered=context_gathered,
    )
    is_success = bool(check.get("is_success"))
    turn_index = int(state.get("turn_count") or 0)
    location_id = str(state.get("current_location") or "hazard_lab")

    flags["hazard_lab_diary_read"] = True
    flags["hazard_lab_diary_decoded"] = is_success
    flags["act3_secret_study_entered"] = True
    flags["act3_diary_read"] = True
    flags["act3_diary_decoded"] = is_success
    flags["act3_gatekeeper_potion_truth_known"] = is_success

    participants = [actor_id]
    entities = state.get("entities") if isinstance(state.get("entities"), dict) else {}
    for member_id in ("analyst", "scout", "tactician"):
        if member_id in entities:
            participants.append(member_id)

    pending_events: list[Dict[str, Any]] = []
    if is_success:
        flags["hazard_lab_antidote_formula_fragment_known"] = {
            "value": True,
            "visibility": {
                "scope": "actor",
                "actors": [actor_id],
                "reason": "diary_decoded_private_fragment",
            },
        }
        flags["hazard_lab_key_hint_known"] = {
            "value": True,
            "visibility": {
                "scope": "party",
                "reason": "diary_decoded_party_hint",
            },
        }
        flags["act3_heavy_key_hint_known"] = True
        flags["act3_party_knows_gatekeeper_truth"] = True

        private_text = (
            "我读懂了：Gatekeeper 因实验药剂变得聪明但极不稳定，毒气陷阱会触发他的警觉，"
            "逃生关键是 heavy_iron_key。日记末尾写着“解药配方其实就在……”却被血迹打断。"
        )
        party_text = "队伍确认：Gatekeeper、实验药剂、毒气陷阱与 heavy_iron_key 互相连接，钥匙是逃离实验室的关键。"
        pending_events = [
            _build_memory_event(
                event_id=f"lore:diary:{actor_id}:{turn_index}:private",
                actor_id=actor_id,
                turn_index=turn_index,
                visibility="actor",
                scope="actor_private",
                text=private_text,
                location_id=location_id,
                participants=[actor_id],
            ),
            _build_memory_event(
                event_id=f"lore:diary:{actor_id}:{turn_index}:party",
                actor_id=actor_id,
                turn_index=turn_index,
                visibility="party",
                scope="party_shared",
                text=party_text,
                location_id=location_id,
                participants=participants,
            ),
        ]
        narrator_text = (
            "他读出了完整危险知识：Gatekeeper 因实验药剂畸变后变得聪明且不稳定，"
            "毒气陷阱会触发他的警觉，逃生关键是 heavy_iron_key。"
            "最后一页留下“解药配方其实就在……”的中断线索。"
        )
        monologue = "这本血污日记把机关全写死了，只差最后那半句解药配方。"
        objective_update = "[目标更新] 找到 Gatekeeper。他持有重铁钥匙，并且知道毒气防线的真相。"
    else:
        flags.pop("hazard_lab_antidote_formula_fragment_known", None)
        flags.pop("hazard_lab_key_hint_known", None)
        flags.pop("act3_party_knows_gatekeeper_truth", None)
        flags.pop("act3_heavy_key_hint_known", None)
        fragment_text = "我只辨认出碎片词句：训练无人机、箱子、毒气，没法拼出完整线索。"
        pending_events = [
            _build_memory_event(
                event_id=f"lore:diary:{actor_id}:{turn_index}:fragment",
                actor_id=actor_id,
                turn_index=turn_index,
                visibility="actor",
                scope="actor_private",
                text=fragment_text,
                location_id=location_id,
                participants=[actor_id],
            )
        ]
        narrator_text = "他只从血污字迹里看出零碎词句：训练无人机、箱子、毒气，仍无法解读完整危险知识。"
        monologue = "字迹像被酸液灼过，只剩训练无人机、箱子、毒气几个词。"
        objective_update = "[目标更新] 找到持钥匙的训练无人机。书房笔记暗示他和毒气有关。"

    latest_roll = {
        "intent": "READ",
        "target": "hazard_diary",
        "skill": str(check.get("skill") or "intelligence"),
        "dc": int(check.get("dc") or DIARY_DECODE_DC),
        "modifier": int(check.get("modifier") or 0),
        "result": dict(check.get("result") or {}),
    }
    return {
        "flags": flags,
        "pending_events": pending_events,
        "latest_roll": latest_roll,
        "narrator_text": narrator_text,
        "character_monologue": monologue,
        "decoded": is_success,
        "objective_update": objective_update,
    }


def _fallback_read_payload(
    *,
    actor_name: str,
    int_score: int,
    title: str,
    raw_text: str,
) -> Dict[str, str]:
    lowered_text = raw_text.lower()
    diary_like = (
        _normalize_id(title).find("日记") >= 0
        or "gatekeeper" in lowered_text
        or "铁钥匙" in raw_text
        or "毒气陷阱" in raw_text
    )

    if diary_like:
        if int_score >= 14:
            return {
                "narrator_text": (
                    f"他迅速读懂了《{title}》的布局：Gatekeeper 与毒气陷阱联动，"
                    "沉重铁钥匙被藏在密室箱子里。"
                ),
                "character_monologue": (
                    "这位危害研究员把机关写在日记里，真是把愚蠢刻进了墓志铭。"
                ),
            }
        if int_score < 10:
            return {
                "narrator_text": (
                    f"他翻着《{title}》只抓到零碎词句：训练无人机、箱子、毒气。"
                ),
                "character_monologue": "字写得像尸斑，我只看懂‘训练无人机’和‘箱子’。"
            }
        return {
            "narrator_text": "他大致看明白了：通道毒气会惊动 Gatekeeper，钥匙在密室箱子。",
            "character_monologue": "机关不复杂，真正可怕的是写日志的人自信过头。",
        }

    if int_score < 10:
        return {
            "narrator_text": (
                f"他翻阅《{title}》时被术语和污渍打乱思路，只拼出“训练无人机失控”和“走廊毒气”的只言片语。"
            ),
            "character_monologue": "这堆涂鸦看得我头疼。"
        }

    if "控制阀口令" in raw_text and "Control Valve Phrase" in raw_text:
        return {
            "narrator_text": (
                f"他读懂了《{title}》：训练无人机喝药后变聪明并被锁在主控室，走廊布有毒气陷阱，"
                "通行密语是“控制阀口令 (Control Valve Phrase)”。"
            ),
            "character_monologue": "把口令写进日志，这种防御天赋值得被钉在公告墙上。"
        }
    return {
        "narrator_text": f"他读完《{title}》，迅速提炼出其中关于机关与钥匙位置的关键信息。",
        "character_monologue": "线索够用了，剩下就是动手。",
    }


def _build_dynamic_read_prompt(
    *,
    actor_name: str,
    actor_profile: str,
    int_score: int,
    title: str,
    raw_text: str,
) -> str:
    profile_text = actor_profile or "冷静、谨慎、习惯先观察再行动"
    return (
        "你现在是controlled agent simulation的地下城主 (DM)。\n"
        f"玩家角色：{actor_name} - {profile_text}。\n"
        f"当前智力值 (INT)：{int_score}。\n"
        f"他正在阅读一本《{title}》。\n\n"
        f"日记事实内容：{raw_text}\n\n"
        "请基于角色的性格和智力值，生成一段他阅读后的内心独白或直接的口头吐槽（100字以内）。\n"
        "- 如果 INT >= 14：他能立刻看透布局，并刻薄地嘲笑陷阱破绽。\n"
        "- 如果 INT < 10：他可能对枯燥文字不耐烦，只提取到“训练无人机”和“箱子”等关键词。\n"
        "- 必须以 JSON 格式返回："
        '{"narrator_text": "系统描述...", "character_monologue": "角色的台词..."}'
    )


def _generate_read_payload(
    *,
    actor_name: str,
    actor_profile: str,
    int_score: int,
    title: str,
    raw_text: str,
) -> Dict[str, str]:
    fallback = _fallback_read_payload(
        actor_name=actor_name,
        int_score=int_score,
        title=title,
        raw_text=raw_text,
    )
    if not settings.API_KEY:
        return fallback

    prompt = _build_dynamic_read_prompt(
        actor_name=actor_name,
        actor_profile=actor_profile,
        int_score=int_score,
        title=title,
        raw_text=raw_text,
    )

    try:
        client = OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
        completion = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=260,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        content = completion.choices[0].message.content if completion.choices else ""
        parsed = parse_llm_json(content or "")
        narrator_text = str(parsed.get("narrator_text") or "").strip()
        character_monologue = str(parsed.get("character_monologue") or "").strip()
        if narrator_text or character_monologue:
            return {
                "narrator_text": narrator_text or fallback["narrator_text"],
                "character_monologue": character_monologue or fallback["character_monologue"],
            }
    except Exception as exc:
        logger.warning("lore narration generation timed out/failed, static fallback applied: %s", exc)
        return dict(LORE_TIMEOUT_FALLBACK)

    return fallback


def lore_node(state: GameState) -> Dict[str, Any]:
    intent = str(state.get("intent", "CHAT") or "CHAT").strip().upper()
    if intent != "READ":
        return {}

    entities = copy.deepcopy(state.get("entities") or {})
    environment_objects = copy.deepcopy(state.get("environment_objects") or {})
    intent_context = state.get("intent_context") or {}

    actor_id = _normalize_id(intent_context.get("action_actor") or "player")
    target_id = _normalize_id(intent_context.get("action_target"))
    if not actor_id:
        actor_id = "player"

    actor = entities.get(actor_id)
    if not isinstance(actor, dict):
        actor_id = "player"
        actor = entities.get("player")
    if not isinstance(actor, dict):
        return {
            "journal_events": ["❌ [阅读] 无法判定阅读者。"],
            "entities": entities,
            "environment_objects": environment_objects,
        }

    resolved_target_id, target_obj = _resolve_readable_target(
        environment_objects=environment_objects,
        target_id=target_id,
    )
    if not resolved_target_id or not isinstance(target_obj, dict):
        return {
            "journal_events": [f"❌ [阅读] 未找到可阅读目标：{target_id or 'unknown'}。"],
            "entities": entities,
            "environment_objects": environment_objects,
        }

    object_type = _normalize_id(target_obj.get("type"))
    if object_type != "readable":
        return {
            "journal_events": [f"❌ [阅读] 目标 {resolved_target_id} 不是可阅读对象。"],
            "entities": entities,
            "environment_objects": environment_objects,
        }

    lore_id = _normalize_id(target_obj.get("lore_id"))
    lore_db = _load_lore_db()
    lore_entry = lore_db.get(lore_id, {})
    if not isinstance(lore_entry, dict) or not lore_entry:
        return {
            "journal_events": [f"❌ [阅读] 文本条目缺失：{lore_id or 'unknown'}。"],
            "entities": entities,
            "environment_objects": environment_objects,
        }

    title = str(lore_entry.get("title") or target_obj.get("name") or resolved_target_id)
    raw_text = str(lore_entry.get("raw_text") or "").strip()
    if not raw_text:
        return {
            "journal_events": [f"❌ [阅读] 《{title}》没有可读内容。"],
            "entities": entities,
            "environment_objects": environment_objects,
        }

    actor_name = _display_entity_name(actor_id, actor)
    int_score = _extract_actor_int(actor)
    flags_patch = dict(state.get("flags") or {})
    pending_events: list[Dict[str, Any]] = []
    latest_roll = dict(state.get("latest_roll") or {})

    if lore_id in DIARY_LORE_IDS:
        diary_resolution = _build_diary_resolution(
            state=state,
            actor_id=actor_id,
            actor=actor,
            int_score=int_score,
        )
        narrator_text = str(diary_resolution.get("narrator_text") or "").strip()
        character_monologue = str(diary_resolution.get("character_monologue") or "").strip()
        flags_patch = dict(diary_resolution.get("flags") or flags_patch)
        pending_events = list(diary_resolution.get("pending_events") or [])
        latest_roll = dict(diary_resolution.get("latest_roll") or latest_roll)
        objective_update = str(diary_resolution.get("objective_update") or "").strip()
        extra_journal_events: list[str] = []
    else:
        extra_journal_events = []
        actor_profile = _extract_actor_personality(actor)
        read_payload = _generate_read_payload(
            actor_name=actor_name,
            actor_profile=actor_profile,
            int_score=int_score,
            title=title,
            raw_text=raw_text,
        )
        narrator_text = str(read_payload.get("narrator_text") or "").strip()
        character_monologue = str(read_payload.get("character_monologue") or "").strip()
        if not narrator_text:
            narrator_text = _fallback_read_payload(
                actor_name=actor_name,
                int_score=int_score,
                title=title,
                raw_text=raw_text,
            )["narrator_text"]
        objective_update = ""
        if resolved_target_id == "chemical_notes" or lore_id == "hazard_chemical_notes":
            flags_patch["act3_chemical_notes_seen"] = True
            flags_patch["act3_secret_study_entered"] = True
            flags_patch["act3_diary_context_gathered"] = True
            flags_patch["act3_diary_context_bonus"] = 10
            objective_update = "[书房观察] analyst -> necromancy_pollution"
            extra_journal_events.append("[线索整合] chemical_notes -> diary_context")
            extra_journal_events.append("这些药剂笔记让日记里的术语变得可读。")
        elif resolved_target_id == "iron_key_sketch" or lore_id == "hazard_iron_key_sketch":
            flags_patch["act3_key_sketch_seen"] = True
            flags_patch["act3_heavy_key_hint_known"] = True
            flags_patch["act3_secret_study_entered"] = True
            flags_patch["act3_diary_context_gathered"] = True
            flags_patch["act3_diary_context_bonus"] = 10
            objective_update = "[书房观察] scout -> practical_clues"
            extra_journal_events.append("[线索整合] iron_key_sketch -> diary_context")
            extra_journal_events.append("钥匙草图把日记里的逃生线索串了起来。")
    base_lore_text = raw_text
    final_output = (
        f"📜 [原文] {base_lore_text}\n\n"
        f"📖 [动作] {narrator_text}\n"
        f"💬 [独白] {character_monologue}"
    )

    journal_events = [final_output]
    if objective_update:
        journal_events.append(objective_update)
    journal_events.extend(extra_journal_events)

    patch: Dict[str, Any] = {
        "journal_events": journal_events,
        "entities": entities,
        "environment_objects": environment_objects,
        "flags": flags_patch,
    }
    if pending_events:
        patch["pending_events"] = pending_events
    if latest_roll:
        patch["latest_roll"] = latest_roll
    return patch
