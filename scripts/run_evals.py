#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.eval.runner import run_eval_suite_sync


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Controlled Agent eval cases and emit artifacts.")
    parser.add_argument(
        "--suite",
        default="golden",
        help="Eval suite name. Currently supports: golden",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Optional case selector (session.id or yaml filename stem).",
    )
    parser.add_argument(
        "--eval-dir",
        default="evals/golden",
        help="Directory that contains eval case YAML files.",
    )
    parser.add_argument(
        "--output-root",
        default="artifacts/evals",
        help="Root output directory for generated artifacts.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_eval_suite_sync(
            suite=args.suite,
            eval_dir=args.eval_dir,
            case_selector=args.case,
            output_root=args.output_root,
        )
    except Exception as exc:
        print(f"[Eval] failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(result.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
