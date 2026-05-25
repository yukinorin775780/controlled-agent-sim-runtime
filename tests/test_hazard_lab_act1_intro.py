import copy

from core.actors.builders import build_actor_view
from core.campaigns.hazard_lab import detect_lab_intro_awareness
from core.systems.world_init import get_initial_world_state


def _build_lab_state() -> dict:
    state = get_initial_world_state(map_id="hazard_lab")
    state["flags"] = {}
    state["journal_events"] = []
    return state


def test_detect_lab_intro_awareness_returns_patch_for_hazard_lab():
    state = _build_lab_state()

    patch = detect_lab_intro_awareness(state)

    assert patch is not None
    flags = patch["flags"]
    assert flags["hazard_lab_intro_seen"] is True
    assert flags["world_hazard_lab_intro_entered"] is True
    assert "scout_detected_gas_trap" not in flags
    assert "world_hazard_lab_trap_warned" not in flags
    assert flags["analyst_senses_necromancy"]["value"] is True
    assert flags["analyst_senses_necromancy"]["visibility"]["scope"] == "actor"
    assert flags["analyst_senses_necromancy"]["visibility"]["actors"] == ["analyst"]

    analyst_effects = patch["entities"]["analyst"]["status_effects"]
    assert any(effect.get("type") == "tense" for effect in analyst_effects)

    events = patch["journal_events"]
    assert any("刺鼻" in line for line in events)
    assert not any("Scout" in line and ("陷阱" in line or "机关" in line) for line in events)
    assert any("Analyst" in line and "危害" in line for line in events)


def test_detect_lab_intro_awareness_noop_for_seen_or_non_lab():
    seen_state = _build_lab_state()
    seen_state["flags"]["hazard_lab_intro_seen"] = True
    assert detect_lab_intro_awareness(seen_state) is None

    non_lab_state = _build_lab_state()
    non_lab_state["map_data"]["id"] = "training_range"
    assert detect_lab_intro_awareness(non_lab_state) is None


def test_actor_view_visibility_after_lab_intro_does_not_leak_hidden_trap_metadata():
    state = _build_lab_state()
    patch = detect_lab_intro_awareness(state)
    assert patch is not None

    merged = copy.deepcopy(state)
    merged["flags"] = patch["flags"]
    merged["entities"] = patch["entities"]
    merged["journal_events"] = patch["journal_events"]

    scout_view = build_actor_view(merged, "scout")
    analyst_view = build_actor_view(merged, "analyst")
    tactician_view = build_actor_view(merged, "tactician")

    assert "scout_detected_gas_trap" not in scout_view.visible_flags
    assert "analyst_senses_necromancy" not in scout_view.visible_flags

    assert analyst_view.visible_flags["analyst_senses_necromancy"] is True
    assert "scout_detected_gas_trap" not in analyst_view.visible_flags

    assert "scout_detected_gas_trap" not in tactician_view.visible_flags
    assert "analyst_senses_necromancy" not in tactician_view.visible_flags

    assert "gas_trap_1" not in scout_view.visible_environment_objects
    assert "gas_trap_1" not in analyst_view.visible_environment_objects
    assert "gas_trap_1" not in tactician_view.visible_environment_objects
