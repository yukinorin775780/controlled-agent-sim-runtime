"""
Combat bark generation.
Produces short in-character one-liners for highlight combat events.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from config import settings

logger = logging.getLogger(__name__)
LLM_TIMEOUT_SECONDS = 4.5


_FALLBACK_BARKS: Dict[str, str] = {
    "CRITICAL_HIT": "漂亮一击",
    "CRITICAL_MISS": "失手了",
    "KILL": "结束了",
    "ENVIRONMENTAL_SHOVE": "请你吃火",
}


def _sanitize_bark_text(text: str) -> str:
    stripped = str(text or "").strip().strip("\"'“”")
    if not stripped:
        return ""
    one_line = stripped.splitlines()[0].strip()
    # Hard length cap requested by design: <= 10 chars.
    if len(one_line) > 10:
        one_line = one_line[:10]
    return one_line


def _fallback_bark(event_type: str) -> str:
    normalized = str(event_type or "").strip().upper()
    return _FALLBACK_BARKS.get(normalized, "看招")


def generate_combat_bark(
    character_name: str,
    event_type: str,
    target_name: str,
    context: Dict[str, Any],
) -> str:
    """
    Generate a one-line combat bark.
    Falls back to deterministic local text when LLM is unavailable.
    """
    normalized_event = str(event_type or "").strip().upper()
    if not normalized_event:
        return ""

    if not settings.API_KEY:
        return _fallback_bark(normalized_event)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
        system_prompt = (
            f"你正在扮演{character_name}。"
            "请根据发生的战斗事件，用一句话（10个字以内）表达你的临场反应。"
            "必须符合角色性格。不要有任何前言后语，直接输出台词本身。"
        )
        user_prompt = (
            f"事件类型: {normalized_event}\n"
            f"目标: {target_name}\n"
            f"上下文: {context}\n"
        )
        completion = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=24,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        raw_text = completion.choices[0].message.content if completion.choices else ""
        text = _sanitize_bark_text(raw_text or "")
        return text or _fallback_bark(normalized_event)
    except Exception as exc:
        logger.warning("generate_combat_bark failed, fallback applied: %s", exc)
        return _fallback_bark(normalized_event)
