from __future__ import annotations

import json
import math
import re
from typing import Any


_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x4E00 <= codepoint <= 0x9FFF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def estimate_text_tokens(text: str) -> int:
    """
    Deterministic local token estimate for benchmark comparisons.

    It is intentionally conservative and dependency-free:
    - CJK characters count roughly as one token each.
    - ASCII words/numbers count at about four characters per token.
    - punctuation and JSON structure add a small overhead.
    """
    value = str(text or "")
    if not value:
        return 0

    cjk_count = sum(1 for char in value if _is_cjk(char))
    ascii_word_tokens = sum(math.ceil(len(match.group(0)) / 4) for match in _ASCII_WORD_RE.finditer(value))
    punctuation_count = sum(
        1
        for char in value
        if not char.isspace() and not _is_cjk(char) and not char.isalnum() and char != "_"
    )
    return max(1, int(cjk_count + ascii_word_tokens + math.ceil(punctuation_count / 4)))


def estimate_payload_tokens(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return estimate_text_tokens(text)
