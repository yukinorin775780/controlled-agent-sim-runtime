from __future__ import annotations

import pytest

from core.eval.models import EvalCase, FakeClock, ScriptedLlm, ScriptedRng
from core.eval.replay import LlmPatchSpec, apply_replay_patches


def _make_case() -> EvalCase:
    return EvalCase.from_dict(
        {
            "session": {"id": "replay_case"},
            "determinism": {
                "strict": True,
                "perf_counter": [1.0, 2.0],
                "now_iso": ["2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00"],
                "randint": [17],
                "choice_indices": [1],
                "random_values": [0.25],
                "llm": {
                    "dm": [{"intent": "CHAT"}],
                },
            },
            "steps": [{"id": "s1", "intent": "init_sync"}],
            "expected": {},
        }
    )


def test_fake_clock_is_stable_and_exhaustion_is_deterministic():
    strict_clock = FakeClock(
        perf_counter_script=[0.1, 0.2],
        now_iso_script=["2026-01-01T00:00:00+00:00"],
        strict=True,
    )
    assert strict_clock.perf_counter() == 0.1
    assert strict_clock.perf_counter() == 0.2
    with pytest.raises(RuntimeError):
        strict_clock.perf_counter()

    non_strict = FakeClock(
        perf_counter_script=[0.5],
        now_iso_script=["2026-01-01T00:00:00+00:00"],
        strict=False,
    )
    assert non_strict.perf_counter() == 0.5
    assert non_strict.perf_counter() == pytest.approx(0.501, abs=1e-9)
    assert non_strict.now().isoformat() == "2026-01-01T00:00:00+00:00"
    assert non_strict.now().isoformat() == "2026-01-01T00:00:00.001000+00:00"


def test_scripted_rng_is_stable_for_randint_choice_and_random():
    rng = ScriptedRng(
        randint_script=[5],
        choice_indices_script=[2],
        random_values_script=[0.8],
        strict=True,
    )
    assert rng.randint(1, 10) == 5
    assert rng.choice(["a", "b", "c"]) == "c"
    assert rng.random() == pytest.approx(0.8, abs=1e-9)
    with pytest.raises(RuntimeError):
        rng.randint(1, 10)


def test_scripted_llm_is_stable_and_enforces_script_exhaustion():
    llm = ScriptedLlm(script={"dm": [{"intent": "CHAT"}]}, strict=True)
    assert llm.respond(channel="dm") == {"intent": "CHAT"}
    with pytest.raises(RuntimeError):
        llm.respond(channel="dm")


def test_apply_replay_patches_applies_clock_rng_and_llm_stubs():
    ctx = _make_case().determinism
    replay_ctx = _make_case()
    from core.eval.models import ReplayContext

    built = ReplayContext.from_case(replay_ctx)

    with apply_replay_patches(
        built,
        llm_specs=(LlmPatchSpec(target="core.llm.dm.analyze_intent", channel="dm"),),
    ):
        import core.application.game_service as game_service_mod
        import core.graph.graph_routers as routers_mod
        import core.llm.dm as dm_mod
        import core.systems.dice as dice_mod

        _ = ctx  # keep deterministic fixture explicit
        assert game_service_mod.time.perf_counter() == 1.0
        assert game_service_mod.time.perf_counter() == 2.0
        assert dice_mod.random.randint(1, 20) == 17
        assert routers_mod.random.random() == pytest.approx(0.25, abs=1e-9)
        assert dm_mod.analyze_intent("hello") == {"intent": "CHAT"}
