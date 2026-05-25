"""Story-rule rendering smoke test for Analyst."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from characters.loader import load_character


def _render_analyst_prompt(current_flags: dict) -> str:
    analyst = load_character("analyst")
    attrs = analyst.data.copy()
    attrs["relationship"] = 50
    return analyst.loader.render_prompt(
        name=analyst.name,
        attributes=attrs,
        current_flags=current_flags,
        flags={},
        summary="",
        journal_entries=[],
        inventory_items=["healing_potion"],
        has_healing_potion=True,
        time_of_day="morning",
        hp=20,
        active_buffs=[],
        relationship_score=50,
        affection=50,
        protocol_confidence=70,
        memory_integrity=60,
    )


def test_analyst_story_rule_renders_when_artifact_is_disclosed():
    prompt = _render_analyst_prompt({"artifact_confessed": True})

    assert "[CURRENT STORY STATE]" in prompt
    assert "[STORY STATE: ARTIFACT DISCLOSED]" in prompt
