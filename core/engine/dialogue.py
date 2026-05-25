from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import settings


def _client():
    if not settings.API_KEY:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    try:
        return OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
    except Exception:
        return None


def generate_dialogue(system_prompt: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
    """Generate dialogue through the configured provider, with a deterministic fallback."""
    conversation_history = conversation_history or []
    client = _client()
    if client is None:
        return "(No live LLM is configured for this local run.)"

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history)
    try:
        completion = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.7,
            max_tokens=500,
        )
        content = completion.choices[0].message.content
        return content if content else "(The agent stays silent.)"
    except Exception:
        return "(The agent stays silent.)"


def update_summary(current_summary: str, recent_history: List[Dict[str, Any]]) -> str:
    """Update a compact third-person summary through the configured provider."""
    client = _client()
    if client is None:
        return current_summary

    history_text = ""
    for msg in recent_history:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "user":
            history_text += f"Player: {content}\n"
        elif role == "assistant":
            history_text += f"Agent: {content}\n"

    if current_summary:
        prompt = (
            f"Here is the previous story summary: '{current_summary}'\n\n"
            f"Here are the recent events:\n{history_text}\n"
            "Condense the recent events into the summary, keeping it concise and in third person."
        )
    else:
        prompt = (
            f"Here are recent events:\n{history_text}\n"
            "Create a concise third-person summary of key events and relationship dynamics."
        )

    try:
        completion = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],  # type: ignore[arg-type]
            temperature=0.3,
            max_tokens=300,
        )
        content = completion.choices[0].message.content
        return content.strip() if content else current_summary
    except Exception:
        return current_summary


def parse_ai_response(response_text: str) -> Dict[str, Any]:
    """Parse lightweight control tags from an LLM response."""
    if not response_text:
        return {"thought": None, "approval": 0, "new_state": None, "action": None, "text": ""}

    text = response_text.strip()
    approval = 0
    new_state = None
    action = None
    thought = None

    thought_pattern = r"\[THOUGHT\](.*?)\[/THOUGHT\]"
    thought_match = re.search(thought_pattern, text, re.IGNORECASE | re.DOTALL)
    if thought_match:
        thought = thought_match.group(1).strip()
    cleaned_text = re.sub(thought_pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    approval_pattern = r"\[APPROVAL:\s*([+-]?\d+)\s*\]"
    approval_matches = list(re.finditer(approval_pattern, cleaned_text, re.IGNORECASE))
    if approval_matches:
        approval = max(-5, min(5, int(approval_matches[-1].group(1))))

    state_pattern = r"\[STATE:\s*(SILENT|VULNERABLE|NORMAL)\s*\]"
    state_matches = list(re.finditer(state_pattern, cleaned_text, re.IGNORECASE))
    if state_matches:
        new_state = state_matches[-1].group(1).upper()

    action_pattern = r"\[ACTION:\s*([\w_]+)\s*\]"
    action_matches = list(re.finditer(action_pattern, cleaned_text, re.IGNORECASE))
    if action_matches:
        action = action_matches[-1].group(1).upper()

    cleaned_text = re.sub(approval_pattern, "", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(state_pattern, "", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(action_pattern, "", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip().strip("\"").strip("'")

    return {"thought": thought, "approval": approval, "new_state": new_state, "action": action, "text": cleaned_text}
