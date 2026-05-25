"""
Simulation Director (DM) Module
Analyzes player intent and determines game mechanics (skill checks, DC, etc.)
"""

import ast
import logging
import operator
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound
from openai import OpenAI

from characters.loader import load_character
from config import settings
from core.eval.telemetry import emit_telemetry, extract_token_usage
from core.systems.inventory import get_registry
from core.systems.spells import resolve_spell_id
from core.utils.text_processor import parse_llm_json

logger = logging.getLogger(__name__)
LLM_TIMEOUT_SECONDS = 4.5

DEFAULT_AVAILABLE_NPCS = ["analyst", "scout"]
DEFAULT_TARGET_NPC = DEFAULT_AVAILABLE_NPCS[0]
_COMPARISON_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}
_client: Optional[OpenAI] = None
PLAYER_TARGET_ALIASES = frozenset({"我", "自己", "玩家", "me", "player"})
MOVE_KEYWORDS = ("移动到", "走向", "走到", "靠近", "接近", "过去", "move", "approach", "去")
RETURN_TO_PLAYER_KEYWORDS = (
    "过来",
    "来我这",
    "来这里",
    "到我这",
    "我身边",
    "我旁边",
    "我这里",
    "我这边",
    "comehere",
    "cometome",
)
ATTACK_KEYWORDS = (
    "攻击",
    "砍",
    "砍死",
    "打",
    "杀",
    "干掉",
    "宰了",
    "射击",
    "射箭",
    "开弓",
    "拉弓",
    "attack",
    "hit",
    "strike",
    "shoot",
)
SHOVE_KEYWORDS = (
    "推开",
    "推倒",
    "撞开",
    "撞倒",
    "猛推",
    "shove",
    "push",
    "推",
)
DISARM_KEYWORDS = (
    "解除陷阱",
    "解除绊线陷阱",
    "解除绊线",
    "拆陷阱",
    "拆除陷阱",
    "拆除绊线陷阱",
    "拆雷",
    "排雷",
    "disarm",
)
CAST_SPELL_KEYWORDS = ("施法", "施放", "释放", "吟唱", "cast", "咏唱", "使用")
LOOT_KEYWORDS = ("搜刮", "舔包", "搜尸", "摸尸", "摸尸体", "摸", "拾取", "捡起", "loot")
UNLOCK_KEYWORDS = ("撬开", "解锁", "开锁", "打开", "撬锁", "unlock", "open")
INTERACT_KEYWORDS = ("交互", "互动", "开门", "关门", "推门", "拉门", "打开", "使用", "解开", "interact")
DOOR_HINT_KEYWORDS = ("门", "door", "开门", "关门", "推门", "拉门")
DOOR_ATTACK_MARKERS = ("攻击门", "砸门", "打门", "破门", "attack door", "smash door")
EQUIP_KEYWORDS = ("装备", "拿上", "拿起", "穿上", "佩戴", "equip", "wear")
UNEQUIP_KEYWORDS = ("卸下", "脱下", "取下", "unequip", "remove")
USE_ITEM_KEYWORDS = ("喝下", "喝", "服用", "使用", "use", "drink", "consume")
STEALTH_KEYWORDS = ("潜行", "隐匿", "隐藏", "蹲下", "stealth", "sneak", "hide")
DIALOGUE_START_KEYWORDS = ("交谈", "说话", "沟通", "谈判", "聊聊", "搭话", "谈谈", "talk", "negotiate")
SHORT_REST_KEYWORDS = ("短休", "稍微休息一下", "小憩", "短暂休息", "short rest")
LONG_REST_KEYWORDS = ("长休", "扎营", "睡一觉", "过夜休息", "long rest")
READ_KEYWORDS = (
    "阅读",
    "查看",
    "翻阅",
    "看书",
    "看看书",
    "看看书上",
    "书上写了什么",
    "日记",
    "日志",
    "笔记",
    "read",
    "inspect",
    "examine",
)
END_TURN_KEYWORDS = ("待命", "结束回合", "结束行动", "结束轮次", "跳过回合", "跳过行动", "pass", "end turn")
END_TURN_GROUP_KEYWORDS = ("我方回合", "全员回合", "结束我方", "结束全员", "全员结束", "结束队伍")
PHYSICAL_ACTION_KEYWORDS = (
    "攻击",
    "砍",
    "打",
    "杀",
    "射击",
    "attack",
    "hit",
    "strike",
    "shoot",
    "移动",
    "走向",
    "走到",
    "靠近",
    "move",
    "approach",
    "推开",
    "推倒",
    "shove",
    "push",
    "施法",
    "施放",
    "cast",
    "搜刮",
    "loot",
    "撬开",
    "解锁",
    "开锁",
    "拆陷阱",
    "解除陷阱",
    "开门",
    "关门",
    "阅读",
    "查看",
    "翻阅",
    "read",
    "inspect",
    "examine",
    "装备",
    "卸下",
    "喝下",
    "使用药水",
    "结束回合",
    "待命",
    "拔剑",
    "跑开",
    "撤退",
    "离开",
)
ENTITY_ALIAS_MAP = {
    "analyst": ("analyst", "分析员"),
    "scout": ("scout", "侦察员", "阿斯"),
    "tactician": ("tactician", "战术员", "莱泽尔", "莱埃", "莱泽"),
    "drone_1": ("drone_1", "drone", "训练无人机", "训练无人机", "训练无人机"),
    "player": tuple(PLAYER_TARGET_ALIASES),
    "camp_fire": ("camp_fire", "campfire", "篝火", "营火", "火堆", "fire"),
    "iron_chest": ("iron_chest", "铁箱子", "箱子", "宝箱", "chest"),
    "chest_1": ("chest_1", "study_chest", "书房箱子", "书房的箱子", "书房宝箱", "战利品箱"),
    "locked_chest": ("locked_chest", "上锁宝箱", "上锁箱子", "锁住的箱子", "旅行箱"),
    "door_oak_1": ("door_oak_1", "door", "门", "木门", "橡木门", "沉重的橡木门"),
    "trap_tripwire_1": ("trap_tripwire_1", "陷阱", "绊线陷阱", "绊线", "trap"),
    "journal_1": ("journal_1", "日志", "实验日志", "残破的实验日志", "笔记", "journal", "note"),
    "hazard_diary": (
        "hazard_diary",
        "日记",
        "日记本",
        "危害研究员日记",
        "沾满血污的日记本",
        "血污日记",
        "书",
        "书上",
        "diary",
        "book",
    ),
    "gatekeeper": ("gatekeeper", "守门人", "变异训练无人机萨满", "训练无人机萨满", "gatekeeper the mutated"),
}
ITEM_ALIAS_MAP = {
    "scimitar": ("scimitar", "弯刀"),
    "rusty_dagger": ("rusty_dagger", "生锈匕首", "匕首"),
    "shortbow": ("shortbow", "短弓", "弓"),
    "crossbow": ("crossbow", "轻弩", "弩"),
    "mace": ("mace", "钉头锤", "锤"),
    "healing_potion": ("healing_potion", "治疗药水", "药水"),
}
SPELL_ALIAS_MAP = {
    "sacred_flame": ("sacred_flame", "sacred flame", "圣火", "圣火术"),
    "thunderwave": ("thunderwave", "thunder wave", "雷鸣波", "雷鸣术"),
}


# 初始化 Jinja2 环境（用于加载 DM prompt 模板）
# 获取当前文件所在目录（core/llm/），然后指向 core/llm/prompts/
_core_dir = os.path.dirname(os.path.abspath(__file__))
_prompts_dir = os.path.join(_core_dir, "prompts")

_jinja_env = Environment(
    loader=FileSystemLoader(_prompts_dir),
    trim_blocks=True,
    lstrip_blocks=True
)


class RuleEvaluationError(ValueError):
    """Raised when a narrative rule contains unsupported syntax."""


def _create_openai_client() -> OpenAI:
    """Create the OpenAI client only when DM analysis actually needs it."""
    if not settings.API_KEY:
        raise RuntimeError(
            "未找到 API Key。请配置 BAILIAN_API_KEY 或 DASHSCOPE_API_KEY 环境变量。"
        )

    try:
        return OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
    except Exception as exc:
        raise RuntimeError(f"初始化 AI 客户端失败: {exc}")


def _get_openai_client() -> OpenAI:
    """Return a cached OpenAI client with lazy initialization."""
    global _client
    if _client is None:
        _client = _create_openai_client()
    return _client


def load_dm_template() -> Template:
    """
    Load the DM prompt template.
    
    Returns:
        jinja2.Template: The loaded DM prompt template
    
    Raises:
        TemplateNotFound: If the template file doesn't exist
    """
    try:
        template = _jinja_env.get_template("dm.j2")
        return template
    except TemplateNotFound:
        raise TemplateNotFound(
            f"DM template not found: {os.path.join(_prompts_dir, 'dm.j2')}"
        )


def parse_json_response(text: str) -> Dict[str, Any]:
    """
    Parse JSON from LLM response text.
    委托给 parse_llm_json，自动剥离 Markdown 代码块，解析失败时返回空字典。
    """
    return parse_llm_json(text)


def _evaluate_rule_node(node: ast.AST, context: Mapping[str, Any]) -> Any:
    """Safely evaluate a restricted AST used by narrative rule conditions."""
    if isinstance(node, ast.BoolOp):
        values = [_evaluate_rule_node(value, context) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise RuleEvaluationError(f"Unsupported boolean operator: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left = _evaluate_rule_node(node.left, context)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _evaluate_rule_node(comparator, context)
            comparator_fn = _COMPARISON_OPERATORS.get(type(op_node))
            if comparator_fn is None:
                raise RuleEvaluationError(
                    f"Unsupported comparison operator: {type(op_node).__name__}"
                )
            if not comparator_fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_evaluate_rule_node(node.operand, context))

    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        raise RuleEvaluationError(f"Unknown variable: {node.id}")

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.List):
        return [_evaluate_rule_node(element, context) for element in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_evaluate_rule_node(element, context) for element in node.elts)

    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "flags":
            flags = context.get("flags", {})
            if isinstance(flags, Mapping):
                return flags.get(node.attr)
            raise RuleEvaluationError("flags must be a mapping for attribute access")
        raise RuleEvaluationError(f"Unsupported attribute access: {ast.dump(node)}")

    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "flags"
            and node.func.attr == "get"
        ):
            flags = context.get("flags", {})
            if not isinstance(flags, Mapping):
                raise RuleEvaluationError("flags must be a mapping for get() access")
            args = [_evaluate_rule_node(argument, context) for argument in node.args]
            if len(args) == 1:
                return flags.get(args[0])
            if len(args) == 2:
                return flags.get(args[0], args[1])
            raise RuleEvaluationError("flags.get only supports one or two arguments")
        raise RuleEvaluationError(f"Unsupported function call: {ast.dump(node)}")

    raise RuleEvaluationError(f"Unsupported AST node: {type(node).__name__}")


def _evaluate_rule_condition(condition: str, context: Mapping[str, Any]) -> bool:
    """Safely evaluate a narrative rule condition against the provided context."""
    normalized_condition = str(condition or "").strip()
    if not normalized_condition:
        return False

    try:
        expression = ast.parse(normalized_condition, mode="eval")
        return bool(_evaluate_rule_node(expression.body, context))
    except (SyntaxError, RuleEvaluationError, TypeError, ValueError) as exc:
        logger.warning("Unsupported rule expression '%s': %s", normalized_condition, exc)
        return False


def _is_reserved_non_npc(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return True
    return normalized in {"player", "unknown"} or normalized.startswith("unknown")


def _load_character_if_exists(name: str):
    normalized = str(name or "").strip().lower()
    if _is_reserved_non_npc(normalized):
        return None
    try:
        return load_character(normalized)
    except (FileNotFoundError, OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Skip narrative rule character loading for '%s': %s",
            normalized,
            exc,
        )
        return None


def _is_loadable_npc(name: str) -> bool:
    return _load_character_if_exists(name) is not None


def _pick_narrative_rule_target(
    analysis: Dict[str, Any],
    *,
    available_npcs: Optional[List[str]] = None,
) -> Optional[str]:
    available_npcs_set = {
        str(npc or "").strip().lower()
        for npc in (available_npcs or [])
        if str(npc or "").strip()
    }
    candidates: List[str] = []
    action_target = str(analysis.get("action_target", "") or "").strip().lower()
    if action_target:
        candidates.append(action_target)

    responders = analysis.get("responders")
    if isinstance(responders, list):
        for responder in responders:
            normalized = str(responder or "").strip().lower()
            if normalized:
                candidates.append(normalized)

    action_actor = str(analysis.get("action_actor", "") or "").strip().lower()
    if action_actor:
        candidates.append(action_actor)

    if available_npcs:
        candidates.extend(
            str(npc or "").strip().lower()
            for npc in available_npcs
            if str(npc or "").strip()
        )

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or _is_reserved_non_npc(candidate):
            continue
        if available_npcs_set and candidate not in available_npcs_set:
            continue
        seen.add(candidate)
        if _is_loadable_npc(candidate):
            return candidate
    return None


def _normalize_safe_responders(
    responders: Any,
    *,
    available_npcs: List[str],
) -> List[str]:
    normalized_available = [
        str(npc).strip().lower()
        for npc in available_npcs
        if str(npc).strip()
    ]
    available_safe = [
        npc for npc in normalized_available if not _is_reserved_non_npc(npc)
    ]

    candidate_raw = responders
    if not isinstance(candidate_raw, list) or len(candidate_raw) == 0:
        candidate_raw = available_safe[:1] or [DEFAULT_TARGET_NPC]

    candidate_responders = [
        str(responder).strip().lower()
        for responder in candidate_raw
        if str(responder).strip()
    ]
    candidate_responders = [
        responder
        for responder in candidate_responders
        if responder in normalized_available and not _is_reserved_non_npc(responder)
    ]
    if not candidate_responders:
        candidate_responders = available_safe[:1] or [DEFAULT_TARGET_NPC]
    return candidate_responders[:1]


def _evaluate_narrative_rules(
    analysis: Dict[str, Any],
    flags: Dict[str, Any],
    target_npc: str = DEFAULT_TARGET_NPC,
) -> Dict[str, Any]:
    """数据驱动的规则引擎：安全解析条件表达式，动态覆盖 DM 判定结果。"""
    char = _load_character_if_exists(target_npc)
    if char is None:
        logger.warning(
            "Narrative rules skipped: target_npc '%s' is not loadable NPC.",
            str(target_npc or "").strip(),
        )
        return analysis
    rules = char.data.get("narrative_rules", [])
    if not rules:
        return analysis

    context: Dict[str, Any] = dict(analysis)
    context["flags"] = flags

    for rule in rules:
        condition_str = rule.get("condition", "False")
        if _evaluate_rule_condition(str(condition_str), context):
            overrides = rule.get("overrides", {})
            if not isinstance(overrides, dict):
                continue
            analysis.update(overrides)
            context.update(overrides)
            logger.info("触发叙事规则覆写: %s", rule.get("id"))

    return analysis


def _normalize_reference_text(value: str) -> str:
    return re.sub(r"[\s_\-，,。.!！？:：]+", "", str(value or "").strip().lower())


def _candidate_aliases(entity_id: str) -> List[str]:
    normalized_id = str(entity_id or "").strip().lower()
    aliases = list(ENTITY_ALIAS_MAP.get(normalized_id, ()))
    aliases.append(normalized_id)
    return [alias for alias in aliases if str(alias).strip()]


def _extract_command_actor(user_input: str, available_npcs: List[str]) -> Optional[str]:
    normalized_text = _normalize_reference_text(user_input)
    if not normalized_text:
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    for actor_id in normalized_npcs:
        if actor_id == "player":
            continue
        for alias in _candidate_aliases(actor_id):
            normalized_alias = _normalize_reference_text(alias)
            if not normalized_alias:
                continue
            alias_position = normalized_text.find(normalized_alias)
            if alias_position < 0:
                continue
            if (
                normalized_text.startswith(f"让{normalized_alias}")
                or normalized_text.startswith(f"叫{normalized_alias}")
                or normalized_text.startswith(f"请{normalized_alias}")
                or normalized_text.startswith(normalized_alias)
            ):
                return actor_id

    return None


def _extract_target_segment(user_input: str, actor_id: str) -> str:
    return _extract_target_segment_for_keywords(user_input, actor_id, MOVE_KEYWORDS)


def _extract_target_segment_for_keywords(user_input: str, actor_id: str, keywords: tuple[str, ...]) -> str:
    normalized_text = _normalize_reference_text(user_input)
    normalized_actor_aliases = [_normalize_reference_text(alias) for alias in _candidate_aliases(actor_id)]
    if actor_id != "player" and any(keyword in normalized_text for keyword in RETURN_TO_PLAYER_KEYWORDS):
        return "player"

    keyword_candidates = sorted(
        [keyword for keyword in keywords if str(keyword).strip()],
        key=lambda kw: len(_normalize_reference_text(kw)),
        reverse=True,
    )
    for keyword in keyword_candidates:
        normalized_keyword = _normalize_reference_text(keyword)
        keyword_position = normalized_text.find(normalized_keyword)
        if keyword_position < 0:
            continue
        segment = normalized_text[keyword_position + len(normalized_keyword):]
        for alias in normalized_actor_aliases:
            if alias and segment.startswith(alias):
                segment = segment[len(alias):]
        for prefix in ("把", "将", "那个", "那只", "那个儿", "这只", "这个"):
            normalized_prefix = _normalize_reference_text(prefix)
            if normalized_prefix and segment.startswith(normalized_prefix):
                segment = segment[len(normalized_prefix):]
        return segment
    return ""


def _resolve_target_id_from_segment(
    *,
    available_targets: List[str],
    actor_id: str,
    normalized_segment: str,
) -> str:
    normalized_targets = [str(target).strip().lower() for target in available_targets if str(target).strip()]
    if normalized_segment in PLAYER_TARGET_ALIASES:
        return "player"

    for candidate in normalized_targets:
        if candidate == actor_id:
            continue
        candidate_id = _normalize_reference_text(candidate)
        if candidate_id and (
            candidate_id in normalized_segment or normalized_segment in candidate_id
        ):
            return candidate

    for candidate in normalized_targets:
        if candidate == actor_id:
            continue
        normalized_aliases = [_normalize_reference_text(alias) for alias in _candidate_aliases(candidate)]
        if any(alias and (alias in normalized_segment or normalized_segment in alias) for alias in normalized_aliases):
            return candidate

    if any(alias in normalized_segment for alias in ("训练无人机", "训练无人机", "drone")):
        for candidate in normalized_targets:
            if candidate != actor_id and candidate.startswith("drone"):
                return candidate

    if any(alias in normalized_segment for alias in ("宝箱", "箱子", "铁箱子", "chest")):
        if "iron_chest" in normalized_targets and actor_id != "iron_chest":
            return "iron_chest"

    if any(alias in normalized_segment for alias in ("篝火", "营火", "火堆", "campfire", "fire")):
        if "camp_fire" in normalized_targets and actor_id != "camp_fire":
            return "camp_fire"

    if any(alias in normalized_segment for alias in ("门", "door", "木门", "橡木门")):
        for candidate in normalized_targets:
            if candidate != actor_id and "door" in candidate:
                return candidate

    return ""


def _resolve_move_target_id(
    *,
    user_input: str,
    available_targets: List[str],
    actor_id: str,
) -> str:
    target_segment = _extract_target_segment(user_input, actor_id)
    normalized_segment = _normalize_reference_text(target_segment)
    return _resolve_target_id_from_segment(
        available_targets=available_targets,
        actor_id=actor_id,
        normalized_segment=normalized_segment,
    )


def _is_door_target_id(target_id: str) -> bool:
    normalized = str(target_id or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("door")
        or "_door" in normalized
        or normalized.endswith("_door")
    )


def _is_probably_readable_target_id(target_id: str) -> bool:
    normalized = str(target_id or "").strip().lower()
    if not normalized:
        return False
    readable_hints = (
        "journal",
        "diary",
        "book",
        "note",
        "lore",
        "readable",
        "日志",
        "日记",
        "笔记",
        "书",
    )
    return any(hint in normalized for hint in readable_hints)


def _resolve_action_target_id(
    *,
    user_input: str,
    available_targets: List[str],
    actor_id: str,
    keywords: tuple[str, ...],
) -> str:
    target_segment = _extract_target_segment_for_keywords(user_input, actor_id, keywords)
    normalized_segment = _normalize_reference_text(target_segment)
    if not normalized_segment:
        normalized_segment = _normalize_reference_text(user_input)
    return _resolve_target_id_from_segment(
        available_targets=available_targets,
        actor_id=actor_id,
        normalized_segment=normalized_segment,
    )


def _build_responders(actor_id: str, available_npcs: List[str]) -> List[str]:
    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    responders = [npc for npc in normalized_npcs if npc != actor_id]
    if actor_id != "player" and actor_id in normalized_npcs:
        responders = [actor_id] + [npc for npc in responders if npc != actor_id]
    return _normalize_safe_responders(responders, available_npcs=available_npcs)


def _build_dialogue_responders(target_id: str, available_npcs: List[str]) -> List[str]:
    normalized_target = str(target_id or "").strip().lower()
    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    if normalized_target and normalized_target in normalized_npcs:
        return _normalize_safe_responders([normalized_target], available_npcs=available_npcs)
    return _normalize_safe_responders(normalized_npcs[:1], available_npcs=available_npcs)


def _is_physical_action_input(user_input: str) -> bool:
    text = str(user_input or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered or keyword in text for keyword in PHYSICAL_ACTION_KEYWORDS)


def _resolve_item_id_from_text(user_input: str) -> str:
    normalized_text = _normalize_reference_text(user_input)
    if not normalized_text:
        return ""

    for item_id, aliases in ITEM_ALIAS_MAP.items():
        if any(_normalize_reference_text(alias) in normalized_text for alias in aliases):
            return item_id

    try:
        all_items = get_registry().all_items()
    except Exception:
        all_items = {}
    for item_id, item_data in all_items.items():
        normalized_id = _normalize_reference_text(item_id)
        normalized_name = _normalize_reference_text(item_data.get("name", ""))
        if normalized_id and normalized_id in normalized_text:
            return str(item_id).strip().lower()
        if normalized_name and normalized_name in normalized_text:
            return str(item_id).strip().lower()

    match = re.search(r"(?:equip|wear|unequip|remove)\s+([a-zA-Z0-9_]+)", user_input, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().lower()
    return ""


def _resolve_spell_id_from_text(user_input: str) -> str:
    normalized_text = _normalize_reference_text(user_input)
    if not normalized_text:
        return ""

    for spell_id, aliases in SPELL_ALIAS_MAP.items():
        if any(_normalize_reference_text(alias) in normalized_text for alias in aliases):
            return spell_id

    return resolve_spell_id(user_input)


def _select_default_hostile_target(available_targets: List[str], actor_id: str) -> str:
    normalized_targets = [str(target).strip().lower() for target in available_targets if str(target).strip()]
    for candidate in normalized_targets:
        if candidate == actor_id:
            continue
        if candidate.startswith("drone") or candidate.startswith("enemy"):
            return candidate
    return ""


def _detect_cast_spell_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    spell_id = _resolve_spell_id_from_text(text)
    if not spell_id:
        return None
    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=CAST_SPELL_KEYWORDS + ATTACK_KEYWORDS,
    )
    if not target_id:
        target_id = _select_default_hostile_target(available_targets, actor_id)

    return {
        "action_type": "CAST_SPELL",
        "difficulty_class": 0,
        "reason": "A character is casting a spell.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
        "spell_id": spell_id,
    }


def _detect_loot_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    """
    轻量规则：前端点击搜刮时的固定文案优先直达 LOOT，避免依赖 LLM 分类。
    """
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in LOOT_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    normalized_targets = [str(target).strip().lower() for target in available_targets if str(target).strip()]
    target_id = ""
    for candidate in normalized_targets:
        if candidate and candidate in lowered:
            target_id = candidate
            break

    if not target_id:
        target_id = _resolve_action_target_id(
            user_input=text,
            available_targets=available_targets,
            actor_id=actor_id,
            keywords=LOOT_KEYWORDS,
        )

    if not target_id:
        match = re.search(r"(?:loot|搜刮|搜尸|摸尸|拾取)\s+([a-zA-Z0-9_]+)", text, flags=re.IGNORECASE)
        if match:
            target_id = match.group(1).strip().lower()

    if not target_id:
        return None

    return {
        "action_type": "LOOT",
        "difficulty_class": 0,
        "reason": "A character is attempting to loot a target.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_attack_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    has_door_hint = any(keyword in lowered or keyword in text for keyword in DOOR_HINT_KEYWORDS)
    has_door_attack_marker = any(marker in lowered or marker in text for marker in DOOR_ATTACK_MARKERS)
    has_door_interact_verb = any(
        keyword in lowered or keyword in text
        for keyword in ("开门", "打开门", "使用钥匙", "heavy_iron_key", "heavy_oak_door_1", "check", "open")
    )
    # 防止“打”误命中“打开门”：门语义默认走 INTERACT，除非玩家明确攻击门。
    if has_door_hint and has_door_interact_verb and not has_door_attack_marker:
        return None
    if not any(keyword in lowered or keyword in text for keyword in ATTACK_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=ATTACK_KEYWORDS,
    )
    if not target_id:
        return None

    return {
        "action_type": "ATTACK",
        "difficulty_class": 0,
        "reason": "A character is attacking a target.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_shove_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in SHOVE_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=SHOVE_KEYWORDS,
    )
    if not target_id:
        return None

    return {
        "action_type": "SHOVE",
        "difficulty_class": 0,
        "reason": "A character is attempting to shove a nearby target.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_equipment_intent(
    user_input: str,
    available_npcs: List[str],
    *,
    is_unequip: bool,
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    keywords = UNEQUIP_KEYWORDS if is_unequip else EQUIP_KEYWORDS
    if not any(keyword in lowered or keyword in text for keyword in keywords):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    item_id = _resolve_item_id_from_text(text)
    if not item_id:
        return None

    intent = "UNEQUIP" if is_unequip else "EQUIP"
    return {
        "action_type": intent,
        "difficulty_class": 0,
        "reason": "A character is changing equipment.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": item_id,
        "item_id": item_id,
    }


def _detect_use_item_intent(
    user_input: str,
    available_npcs: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in USE_ITEM_KEYWORDS):
        return None

    item_id = _resolve_item_id_from_text(text)
    if not item_id:
        return None

    try:
        item_data = get_registry().get_item_data(item_id)
    except Exception:
        item_data = {}
    is_consumable = (
        bool(item_data.get("is_consumable") is True)
        or bool(item_data.get("consumable") is True)
        or str(item_data.get("type", "")).strip().lower() == "consumable"
    )
    if not is_consumable:
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    return {
        "action_type": "USE_ITEM",
        "difficulty_class": 0,
        "reason": "A character is using a consumable item.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": item_id,
        "item_id": item_id,
    }


def _detect_stealth_intent(
    user_input: str,
    available_npcs: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in STEALTH_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    return {
        "action_type": "STEALTH",
        "difficulty_class": 0,
        "reason": "A character is attempting to enter stealth.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": "",
    }


def _detect_start_dialogue_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in DIALOGUE_START_KEYWORDS):
        return None

    actor_id = "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=DIALOGUE_START_KEYWORDS,
    )
    if not target_id or target_id == "player":
        return None

    return {
        "action_type": "START_DIALOGUE",
        "difficulty_class": 0,
        "reason": "The player initiates a direct negotiation or conversation.",
        "is_probing_secret": False,
        "responders": _build_dialogue_responders(target_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_rest_intent(
    user_input: str,
    available_npcs: List[str],
    *,
    is_long_rest: bool,
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    keywords = LONG_REST_KEYWORDS if is_long_rest else SHORT_REST_KEYWORDS
    if not any(keyword in lowered or keyword in text for keyword in keywords):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    action_type = "LONG_REST" if is_long_rest else "SHORT_REST"
    reason = (
        "A character is requesting a long rest to fully recover."
        if is_long_rest
        else "A character is requesting a short rest to recover partially."
    )
    return {
        "action_type": action_type,
        "difficulty_class": 0,
        "reason": reason,
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": "",
    }


def _detect_read_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in READ_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=READ_KEYWORDS,
    )
    if not target_id:
        return None
    if not _is_probably_readable_target_id(target_id):
        return None

    return {
        "action_type": "READ",
        "difficulty_class": 0,
        "reason": "A character is attempting to read a lore document.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_unlock_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in UNLOCK_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=UNLOCK_KEYWORDS,
    )
    if not target_id:
        return None

    return {
        "action_type": "UNLOCK",
        "difficulty_class": 14,
        "reason": "A character is attempting to unlock a locked object.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_disarm_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in DISARM_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=DISARM_KEYWORDS,
    )
    if not target_id:
        return None

    return {
        "action_type": "DISARM",
        "difficulty_class": 15,
        "reason": "A character is attempting to disarm a detected trap.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_interact_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    has_interact_keyword = any(keyword in lowered or keyword in text for keyword in INTERACT_KEYWORDS)
    has_door_hint = any(keyword in lowered or keyword in text for keyword in DOOR_HINT_KEYWORDS)
    if not has_interact_keyword and not has_door_hint:
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_action_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
        keywords=INTERACT_KEYWORDS + UNLOCK_KEYWORDS + MOVE_KEYWORDS,
    )
    if not target_id:
        normalized_targets = [
            str(target).strip().lower()
            for target in available_targets
            if str(target).strip()
        ]
        door_candidates = [target for target in normalized_targets if _is_door_target_id(target)]
        if door_candidates:
            target_id = door_candidates[0]

    if not _is_door_target_id(target_id):
        return None

    return {
        "action_type": "INTERACT",
        "difficulty_class": 0,
        "reason": "A character is interacting with a nearby door.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_move_intent(
    user_input: str,
    available_npcs: List[str],
    available_targets: List[str],
) -> Optional[Dict[str, Any]]:
    """
    轻量规则：移动/靠近类输入优先直达 MOVE，避免依赖 LLM 输出稳定性。
    """
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    move_keywords = (*MOVE_KEYWORDS, *RETURN_TO_PLAYER_KEYWORDS)
    if not any(keyword in lowered or keyword in text for keyword in move_keywords):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    actor_id = _extract_command_actor(text, normalized_npcs) or "player"
    target_id = _resolve_move_target_id(
        user_input=text,
        available_targets=available_targets,
        actor_id=actor_id,
    )

    if not target_id:
        return None

    return {
        "action_type": "MOVE",
        "difficulty_class": 0,
        "reason": "A character is moving toward a target.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": target_id,
    }


def _detect_end_turn_intent(
    user_input: str,
    available_npcs: List[str],
) -> Optional[Dict[str, Any]]:
    text = str(user_input or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in END_TURN_KEYWORDS):
        return None

    normalized_npcs = [str(npc).strip().lower() for npc in available_npcs if str(npc).strip()]
    explicit_actor = _extract_command_actor(text, normalized_npcs)
    actor_id = explicit_actor or "player"
    if explicit_actor:
        action_target = ""
    else:
        action_target = (
            "party"
            if any(keyword in lowered or keyword in text for keyword in END_TURN_GROUP_KEYWORDS)
            or any(keyword in lowered or keyword in text for keyword in END_TURN_KEYWORDS)
            else ""
        )
    return {
        "action_type": "END_TURN",
        "difficulty_class": 0,
        "reason": "A character is skipping their turn.",
        "is_probing_secret": False,
        "responders": _build_responders(actor_id, available_npcs),
        "affection_changes": {},
        "flags_changed": {},
        "item_transfers": [],
        "hp_changes": [],
        "action_actor": actor_id,
        "action_target": action_target,
    }


def analyze_intent(
    user_input: str,
    flags: Optional[Dict[str, Any]] = None,
    time_of_day: str = "晨曦 (Morning)",
    hp: int = 20,
    available_npcs: Optional[List[str]] = None,
    available_targets: Optional[List[str]] = None,
    item_lore: Optional[str] = None,
    active_dialogue_target: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze player intent and determine game mechanics.
    
    Args:
        user_input: The player's input text
        flags: Current world-state flags for context-aware intent analysis and rule engine
        hp: NPC current HP for濒死拦截

    Returns:
        dict: Intent analysis result with keys:
            - action_type: str (DECEPTION, PERSUASION, INTIMIDATION, INSIGHT, ATTACK, NONE)
            - difficulty_class: int (0-30)
            - reason: str (explanation)
    
    Raises:
        RuntimeError: If template loading or LLM call fails
    """
    available_npcs = available_npcs or list(DEFAULT_AVAILABLE_NPCS)
    available_targets = available_targets or list(available_npcs)
    normalized_active_dialogue_target = str(active_dialogue_target or "").strip().lower()

    def _finalize_heuristic_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if result is None:
            return None
        if not normalized_active_dialogue_target:
            return result
        action_type = str(result.get("action_type", "")).strip().upper()
        if action_type in {"CHAT", "START_DIALOGUE", "DIALOGUE_REPLY"}:
            return result
        result["clear_active_dialogue_target"] = True
        if not str(result.get("action_target", "") or "").strip() and action_type in {
            "ATTACK",
            "MOVE",
            "APPROACH",
            "CAST_SPELL",
            "SHOVE",
        }:
            result["action_target"] = normalized_active_dialogue_target
        return result

    # 对话会话锁：除非玩家明确下达物理动作，否则所有输入都视为当前对话回复。
    if normalized_active_dialogue_target and not _is_physical_action_input(user_input):
        return {
            "action_type": "DIALOGUE_REPLY",
            "difficulty_class": 0,
            "reason": "Dialogue session is active; route input to dialogue reply.",
            "is_probing_secret": False,
            "responders": _build_dialogue_responders(normalized_active_dialogue_target, available_npcs),
            "affection_changes": {},
            "flags_changed": {},
            "item_transfers": [],
            "hp_changes": [],
            "action_actor": "player",
            "action_target": normalized_active_dialogue_target,
        }

    start_dialogue_result = _detect_start_dialogue_intent(user_input, available_npcs, available_targets)
    if start_dialogue_result is not None:
        return start_dialogue_result

    unequip_result = _finalize_heuristic_result(
        _detect_equipment_intent(user_input, available_npcs, is_unequip=True)
    )
    if unequip_result is not None:
        return unequip_result
    equip_result = _finalize_heuristic_result(
        _detect_equipment_intent(user_input, available_npcs, is_unequip=False)
    )
    if equip_result is not None:
        return equip_result
    use_item_result = _finalize_heuristic_result(_detect_use_item_intent(user_input, available_npcs))
    if use_item_result is not None:
        return use_item_result
    stealth_result = _finalize_heuristic_result(_detect_stealth_intent(user_input, available_npcs))
    if stealth_result is not None:
        return stealth_result
    long_rest_result = _finalize_heuristic_result(
        _detect_rest_intent(user_input, available_npcs, is_long_rest=True)
    )
    if long_rest_result is not None:
        return long_rest_result
    short_rest_result = _finalize_heuristic_result(
        _detect_rest_intent(user_input, available_npcs, is_long_rest=False)
    )
    if short_rest_result is not None:
        return short_rest_result
    read_result = _finalize_heuristic_result(
        _detect_read_intent(user_input, available_npcs, available_targets)
    )
    if read_result is not None:
        return read_result
    shortcut_result = _finalize_heuristic_result(
        _detect_loot_intent(user_input, available_npcs, available_targets)
    )
    if shortcut_result is not None:
        return shortcut_result
    cast_spell_result = _finalize_heuristic_result(
        _detect_cast_spell_intent(user_input, available_npcs, available_targets)
    )
    if cast_spell_result is not None:
        return cast_spell_result
    shove_result = _finalize_heuristic_result(
        _detect_shove_intent(user_input, available_npcs, available_targets)
    )
    if shove_result is not None:
        return shove_result
    attack_result = _finalize_heuristic_result(
        _detect_attack_intent(user_input, available_npcs, available_targets)
    )
    if attack_result is not None:
        return attack_result
    interact_result = _finalize_heuristic_result(
        _detect_interact_intent(user_input, available_npcs, available_targets)
    )
    if interact_result is not None:
        return interact_result
    disarm_result = _finalize_heuristic_result(
        _detect_disarm_intent(user_input, available_npcs, available_targets)
    )
    if disarm_result is not None:
        return disarm_result
    unlock_result = _finalize_heuristic_result(
        _detect_unlock_intent(user_input, available_npcs, available_targets)
    )
    if unlock_result is not None:
        return unlock_result
    end_turn_result = _finalize_heuristic_result(_detect_end_turn_intent(user_input, available_npcs))
    if end_turn_result is not None:
        return end_turn_result
    move_result = _finalize_heuristic_result(
        _detect_move_intent(user_input, available_npcs, available_targets)
    )
    if move_result is not None:
        return move_result
    if normalized_active_dialogue_target and _is_physical_action_input(user_input):
        lowered = str(user_input or "").lower()
        if any(keyword in lowered or keyword in user_input for keyword in ATTACK_KEYWORDS + ("拔剑",)):
            return {
                "action_type": "ATTACK",
                "difficulty_class": 0,
                "reason": "Dialogue interrupted by direct physical hostility.",
                "is_probing_secret": False,
                "responders": _build_dialogue_responders(normalized_active_dialogue_target, available_npcs),
                "affection_changes": {},
                "flags_changed": {},
                "item_transfers": [],
                "hp_changes": [],
                "action_actor": "player",
                "action_target": normalized_active_dialogue_target,
                "clear_active_dialogue_target": True,
            }

    # 濒死拦截：HP <= 0 时 NPC 已昏迷，跳过 LLM 判定
    if hp <= 0:
        return {
            "action_type": "CHAT",
            "difficulty_class": 0,
            "reason": "NPC is unconscious/dead.",
            "is_probing_secret": False,
        }

    flags = flags or {}
    npcs_str = ", ".join(f'"{n}"' for n in available_npcs)
    targets_str = ", ".join(f'"{t}"' for t in available_targets)
    # Load and render template
    template = load_dm_template()
    prompt = template.render(
        user_input=user_input,
        flags=flags,
        time_of_day=time_of_day,
        available_npcs=npcs_str,
        available_targets=targets_str,
    )
    if item_lore:
        prompt += "\n\n" + item_lore
    
    response_text: Optional[str] = None
    
    try:
        # Call LLM
        messages = [{"role": "user", "content": prompt}]
        client = _get_openai_client()
        llm_started_at = time.perf_counter()
        
        completion = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=messages,  # type: ignore
            temperature=0.3,  # Lower temperature for more consistent analysis
            max_tokens=200,  # DM analysis should be concise
            timeout=LLM_TIMEOUT_SECONDS,
        )
        emit_telemetry(
            "llm_call",
            component="dm",
            provider="openai",
            model=settings.MODEL_NAME,
            success=True,
            duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
            token_usage=extract_token_usage(completion),
        )
        
        response_text = completion.choices[0].message.content
        if not response_text:
            raise RuntimeError("LLM returned empty response")
        
        # Parse JSON from response（防弹解析：失败时返回空字典）
        intent_data = parse_json_response(response_text)

        # 解析失败时兜底，防止游戏崩溃
        if not intent_data:
            return {
                "action_type": "CHAT",
                "difficulty_class": 0,
                "reason": "JSON parse failed, fallback to CHAT.",
                "is_probing_secret": False,
                "responders": available_npcs[:1] or [DEFAULT_TARGET_NPC],
                "affection_changes": {},
                "flags_changed": {},
                "item_transfers": [],
                "hp_changes": [],
            }

        # Validate required fields
        required_fields = ['action_type', 'difficulty_class', 'reason']
        for field in required_fields:
            if field not in intent_data:
                raise ValueError(f"Missing required field in intent analysis: {field}")
        
        # Ensure difficulty_class is an integer
        intent_data['difficulty_class'] = int(intent_data['difficulty_class'])
        
        # Ensure action_type is uppercase
        intent_data['action_type'] = str(intent_data['action_type']).upper()
        intent_data["action_actor"] = str(intent_data.get("action_actor", "player")).strip().lower() or "player"
        intent_data["action_target"] = str(intent_data.get("action_target", "")).strip().lower()
        if normalized_active_dialogue_target and intent_data["action_type"] not in {"CHAT", "START_DIALOGUE", "DIALOGUE_REPLY"}:
            intent_data["clear_active_dialogue_target"] = True
            if not intent_data["action_target"] and intent_data["action_type"] in {
                "ATTACK",
                "MOVE",
                "APPROACH",
                "CAST_SPELL",
                "SHOVE",
            }:
                intent_data["action_target"] = normalized_active_dialogue_target
        if intent_data["action_type"] in {"EQUIP", "UNEQUIP"} and not intent_data.get("item_id"):
            intent_data["item_id"] = _resolve_item_id_from_text(user_input)
        if intent_data["action_type"] in {"USE_ITEM", "CONSUME"} and not intent_data.get("item_id"):
            intent_data["item_id"] = _resolve_item_id_from_text(user_input)
        if intent_data["action_type"] == "CAST_SPELL" and not intent_data.get("spell_id"):
            intent_data["spell_id"] = (
                _resolve_spell_id_from_text(user_input) or resolve_spell_id(intent_data.get("action_target"))
            )
        heuristic_actor = _extract_command_actor(user_input, available_npcs)
        if heuristic_actor and intent_data["action_type"] != "CHAT" and intent_data["action_actor"] == "player":
            intent_data["action_actor"] = heuristic_actor
        if intent_data["action_type"] in {"MOVE", "APPROACH"}:
            heuristic_target = _resolve_move_target_id(
                user_input=user_input,
                available_targets=available_targets,
                actor_id=intent_data["action_actor"],
            )
            if heuristic_target and not intent_data["action_target"]:
                intent_data["action_target"] = heuristic_target
            if intent_data["action_target"] in PLAYER_TARGET_ALIASES:
                intent_data["action_target"] = "player"

        # Topic flag: is_probing_secret (optional, default False)
        intent_data['is_probing_secret'] = bool(intent_data.get('is_probing_secret', False))

        # 多人发言队列：responders（DM 决定的发言顺序）
        responders = _normalize_safe_responders(
            intent_data.get("responders", [DEFAULT_TARGET_NPC]),
            available_npcs=available_npcs,
        )
        intent_data["responders"] = responders

        # 剧情标志位变更：安全提取并过滤
        flags_changed = intent_data.get("flags_changed", {})
        if not isinstance(flags_changed, dict):
            flags_changed = {}
        intent_data["flags_changed"] = {str(k): bool(v) for k, v in flags_changed.items()}

        # 物理物品转移：安全提取并过滤
        item_transfers_raw = intent_data.get("item_transfers", [])
        if not isinstance(item_transfers_raw, list):
            item_transfers_raw = []
        intent_data["item_transfers"] = [
            {"from": str(t.get("from", "player")), "to": str(t.get("to", "")), "item_id": str(t.get("item_id", "")), "count": int(t.get("count", 1))}
            for t in item_transfers_raw if isinstance(t, dict) and t.get("to") and t.get("item_id")
        ]

        # 生命值变动：安全提取并过滤
        hp_changes_raw = intent_data.get("hp_changes", [])
        if not isinstance(hp_changes_raw, list):
            hp_changes_raw = []
        intent_data["hp_changes"] = [
            {"target": str(t.get("target", "")), "amount": int(t.get("amount", 0))}
            for t in hp_changes_raw if isinstance(t, dict) and t.get("target") is not None and isinstance(t.get("amount"), (int, float))
        ]

        # 好感度变化：安全提取并过滤
        # 【为 Dynamic Persona 让路】Analyst 已实装独立的自我反思状态机，DM 不得覆盖其好感度。
        # 即使大模型违反 prompt 输出了 analyst 的 affection_changes，代码层也必须拦截。
        affection_changes = intent_data.get("affection_changes", {})
        if not isinstance(affection_changes, dict):
            affection_changes = {}
        filtered = {}
        for k, v in affection_changes.items():
            npc_id = str(k).strip().lower()
            raw_key = str(k).strip()
            if npc_id not in available_npcs or not isinstance(v, (int, float)):
                continue
            # 黑名单：analyst / 分析员 由 Dynamic Persona 管理，DM 不得覆盖
            if npc_id == "analyst" or "分析员" in raw_key:
                logger.info(
                    "Ignored affection change for Analyst (Dynamic Persona owns this)."
                )
                continue
            filtered[npc_id] = int(v)
        intent_data["affection_changes"] = filtered

        narrative_rule_target = _pick_narrative_rule_target(
            intent_data,
            available_npcs=available_npcs,
        )
        return _evaluate_narrative_rules(
            intent_data,
            flags,
            narrative_rule_target or "",
        )

    except Exception as exc:
        if "llm_started_at" in locals():
            emit_telemetry(
                "llm_call",
                component="dm",
                provider="openai",
                model=settings.MODEL_NAME,
                success=False,
                error_type=exc.__class__.__name__,
                duration_ms=max(0, int(round((time.perf_counter() - llm_started_at) * 1000))),
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
        error_text = str(exc)
        if "API Key" in error_text or "API_KEY" in error_text or "DASHSCOPE_API_KEY" in error_text:
            raise RuntimeError(error_text)
        logger.warning("DM intent analysis timed out/failed, fallback to IDLE: %s", exc)
        responders = _normalize_safe_responders(
            available_npcs[:1] or [DEFAULT_TARGET_NPC],
            available_npcs=available_npcs,
        )
        fallback_target = str(responders[0] if responders else DEFAULT_TARGET_NPC).strip().lower()
        return {
            "action_type": "IDLE",
            "intent": "DIALOGUE",
            "target": fallback_target,
            "dialogue_text": "（他正警惕地盯着你，什么都不想说。）",
            "action_actor": "player",
            "action_target": fallback_target,
            "difficulty_class": 0,
            "reason": "LLM timeout/failure; fallback to IDLE.",
            "is_probing_secret": False,
            "responders": responders,
            "affection_changes": {},
            "flags_changed": {},
            "item_transfers": [],
            "hp_changes": [],
        }
