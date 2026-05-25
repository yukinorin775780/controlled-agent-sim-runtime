from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import yaml

from core.eval.models import EVAL_CASE_YAML_SCHEMA, EvalCase, ReplayContext


def get_eval_case_schema() -> Dict[str, Any]:
    return dict(EVAL_CASE_YAML_SCHEMA)


def load_eval_case(path: str | Path) -> EvalCase:
    case_path = Path(path)
    raw = yaml.safe_load(case_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Eval case must be a mapping: {case_path}")
    return EvalCase.from_dict(raw, source=str(case_path))


def load_eval_cases(paths: Sequence[str | Path]) -> List[EvalCase]:
    return [load_eval_case(item) for item in paths]


def load_eval_cases_from_dir(directory: str | Path) -> List[EvalCase]:
    eval_dir = Path(directory)
    files = sorted(eval_dir.glob("*.yml")) + sorted(eval_dir.glob("*.yaml"))
    return load_eval_cases(files)


def discover_golden_eval_cases(base_dir: str | Path = "evals/golden") -> List[EvalCase]:
    return load_eval_cases_from_dir(base_dir)


def build_replay_context(case: EvalCase) -> ReplayContext:
    return ReplayContext.from_case(case)


def build_replay_context_from_yaml(path: str | Path) -> ReplayContext:
    return build_replay_context(load_eval_case(path))


def dump_eval_case_template() -> Dict[str, Any]:
    """
    Minimal schema-compliant template for evals/golden/*.yaml.
    """
    return {
        "session": {
            "id": "golden_case_001",
            "map_id": "hazard_lab",
            "metadata": {},
        },
        "determinism": {
            "strict": True,
            "perf_counter": [0.001, 0.020, 0.040],
            "now_iso": ["2026-01-01T00:00:00+00:00"],
            "randint": [20, 1, 15],
            "choice_indices": [0, 1],
            "random_values": [0.1, 0.8],
            "llm": {
                "default": [{"text": "scripted response"}],
            },
        },
        "steps": [
            {
                "id": "step_001",
                "user_input": "攻击训练无人机",
                "intent": "ATTACK",
                "character": "player",
                "payload": {},
                "expected": {},
            }
        ],
        "expected": {
            "combat_active": True,
        },
    }


def iter_eval_case_paths(base_dir: str | Path = "evals/golden") -> Iterable[Path]:
    eval_dir = Path(base_dir)
    files = sorted(eval_dir.glob("*.yml")) + sorted(eval_dir.glob("*.yaml"))
    for path in files:
        yield path
