"""
文本处理器：清洗大模型生成的冗余 NPC 剧本前缀，以及 LLM JSON 防弹解析。
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FENCED_BLOCK_RE = re.compile(
    r"```(?P<lang>[^\n`]*)\n?(?P<content>[\s\S]*?)```",
    re.IGNORECASE,
)
_POSITIVE_NUMBER_PREFIX_RE = re.compile(
    r"(?P<prefix>[:\[,]\s*)\+(?P<number>\d+(?:\.\d+)?)"
)


def _iter_json_candidates(raw_text: str) -> list[str]:
    """
    按优先级提取可能的 JSON 候选文本。

    优先级：
    1. ```json fenced block
    2. 其他 fenced block
    3. 普通文本中首个平衡的 JSON 对象/数组
    4. 完整原文
    """
    text = raw_text.strip()
    if not text:
        return []

    candidates: list[str] = []
    json_blocks: list[str] = []
    other_blocks: list[str] = []

    for match in _FENCED_BLOCK_RE.finditer(text):
        content = match.group("content").strip()
        if not content:
            continue

        language = (match.group("lang") or "").strip().lower()
        target_bucket = json_blocks if language == "json" else other_blocks
        if content not in target_bucket:
            target_bucket.append(content)

    candidates.extend(json_blocks)
    candidates.extend(other_blocks)

    balanced_candidate = _find_balanced_json_candidate(text)
    if balanced_candidate and balanced_candidate not in candidates:
        candidates.append(balanced_candidate)

    if text not in candidates:
        candidates.append(text)

    return candidates


def _find_balanced_json_candidate(text: str) -> Optional[str]:
    """扫描文本，提取首个括号平衡的 JSON 对象或数组片段。"""
    for index, char in enumerate(text):
        if char not in "{[":
            continue

        end_index = _find_json_end_index(text, index)
        if end_index is not None:
            return text[index:end_index]

    return None


def _find_json_end_index(text: str, start_index: int) -> Optional[int]:
    """在保留字符串转义语义的前提下，找到 JSON 片段的结束位置。"""
    closing_pairs = {"{": "}", "[": "]"}
    stack = [closing_pairs[text[start_index]]]
    in_string = False
    is_escaped = False

    for index in range(start_index + 1, len(text)):
        char = text[index]

        if in_string:
            if is_escaped:
                is_escaped = False
            elif char == "\\":
                is_escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char in closing_pairs:
            stack.append(closing_pairs[char])
            continue

        if char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return index + 1

    return None


def _sanitize_json_candidate(candidate: str) -> str:
    """
    最小必要清洗：
    去掉对象属性和数组元素里的非法正号前缀，如 : +2、[+1, ...]。
    """
    return _POSITIVE_NUMBER_PREFIX_RE.sub(
        r"\g<prefix>\g<number>",
        candidate.strip(),
    )


def parse_llm_json(raw_text: str) -> dict:
    """
    提取并解析 LLM 返回的 JSON，自动剥离 Markdown 代码块包裹。
    支持容错：清洗 `: +2`、`[+1, ...]` 等非法正数格式，解析失败时返回空字典以保证调用方不崩溃。
    """
    candidates = _iter_json_candidates(raw_text)
    if not candidates:
        return {}

    last_error: Optional[json.JSONDecodeError] = None
    last_candidate = ""

    for candidate in candidates:
        cleaned_candidate = _sanitize_json_candidate(candidate)
        last_candidate = cleaned_candidate
        try:
            parsed: Any = json.loads(cleaned_candidate)
        except json.JSONDecodeError as error:
            last_error = error
            continue

        if isinstance(parsed, dict):
            return parsed

        logger.warning("LLM JSON 顶层不是对象，已回退为空字典: %s", cleaned_candidate)
        return {}

    if last_error is not None:
        logger.warning("LLM 输出了无效的 JSON 格式: %s", last_error)
        logger.warning("清洗后内容: %s", last_candidate)
    return {}  # 兜底返回空字典，保证调用方能够继续运行


def clean_npc_dialogue(speaker: str, raw_text: str) -> str:
    """
    清洗大模型生成的冗余 NPC 名字前缀。
    例如把 "[Analyst]说： 风停了。" 清洗为 "风停了。"
    """
    clean_text = raw_text.strip()

    # 1. 暴力清洗：切掉第一个引号或星号之前的所有废话前缀
    first_quote = clean_text.find('"')
    first_asterisk = clean_text.find('*')
    candidates = [i for i in (first_quote, first_asterisk) if i >= 0]
    if candidates:
        clean_text = clean_text[min(candidates) :].strip()

    # 2. 正则清洗：移除行首出现的类似 "[Analyst]说："、"Analyst: " 等结构
    clean_text = re.sub(
        r"^[：:\s]*\[?[a-zA-Z\u4e00-\u9fa5]+\]?\s*[：:\s说]+", "", clean_text
    ).strip()

    # 3. 兜底清洗：基于传入的 speaker ID 再次强制正则清洗
    clean_text = re.sub(
        rf"^{re.escape(speaker)}\s*[:：说]\s*", "", clean_text, flags=re.IGNORECASE
    ).strip()

    if not clean_text:
        clean_text = raw_text.strip()
    return clean_text


def format_history_message(speaker: str, clean_text: str) -> str:
    """
    为后台历史记录打上标准的说话人标签，防止大模型产生身份幻觉。
    """
    return f"[{speaker}]: {clean_text}"
