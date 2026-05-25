from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from unittest.mock import patch

from core.eval.assertions import AssertionReport, assert_eval_expectations
from core.eval.init import build_replay_context, discover_golden_eval_cases
from core.eval.models import EvalCase, EvalStep, ReplayContext
from core.eval.reporting import (
    ArtifactWriter,
    EvalArtifactPaths,
    build_run_id,
    create_artifact_paths,
    create_telemetry_sink,
)
from core.eval.replay import LlmPatchSpec, apply_replay_patches, default_llm_patch_specs
from core.eval.telemetry import telemetry_scope

try:  # optional runtime dependency in lightweight test/dev environments
    from core.application.game_service import GameService as _GameService
except ModuleNotFoundError:  # pragma: no cover - exercised by command smoke in missing-langgraph env
    _GameService = None

GameService = _GameService


@dataclass(frozen=True)
class RunOptions:
    output_root: str = "artifacts/evals"
    suite: str = "golden"


def _coerce_scripted_content(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if "content" in payload:
            return str(payload.get("content") or "")
        if "reply" in payload:
            return str(payload.get("reply") or "")
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


class _ScriptedChatResponse:
    def __init__(self, payload: Any) -> None:
        self.content = _coerce_scripted_content(payload)
        if isinstance(payload, dict):
            self.tool_calls = list(payload.get("tool_calls") or [])
        else:
            self.tool_calls = []


class _ScriptedChatOpenAI:
    def __init__(self, replay_context: ReplayContext) -> None:
        self._ctx = replay_context

    def bind_tools(self, tools: Any) -> "_ScriptedChatOpenAI":
        _ = tools
        return self

    async def ainvoke(self, messages: Any) -> _ScriptedChatResponse:
        _ = messages
        payload = await self._ctx.llm.arespond(
            channel="generation_chatopenai",
            request={"messages": "redacted"},
        )
        return _ScriptedChatResponse(payload)


def _build_eval_llm_specs() -> List[LlmPatchSpec]:
    specs = list(default_llm_patch_specs())
    specs.extend(
        [
            LlmPatchSpec(target="core.graph.nodes.dm.analyze_intent", channel="dm", is_async=False),
            LlmPatchSpec(
                target="core.graph.nodes.dm.generate_dialogue",
                channel="generation",
                is_async=False,
            ),
            LlmPatchSpec(
                target="core.graph.nodes.generation.generate_dialogue",
                channel="generation",
                is_async=False,
            ),
        ]
    )
    dedup: Dict[str, LlmPatchSpec] = {}
    for spec in specs:
        dedup[spec.target] = spec
    return list(dedup.values())


def _load_suite_cases(*, suite: str, eval_dir: str | Path) -> List[EvalCase]:
    if str(suite).strip().lower() != "golden":
        raise ValueError(f"Unsupported suite: {suite!r}. Currently only 'golden' is supported.")
    return discover_golden_eval_cases(eval_dir)


def _filter_cases(cases: Sequence[EvalCase], case_selector: str | None) -> List[EvalCase]:
    if not case_selector:
        return list(cases)
    selector = str(case_selector).strip().lower()
    selected: List[EvalCase] = []
    for case in cases:
        source_name = Path(case.source).stem.lower() if case.source else ""
        if case.session_id.lower() == selector or source_name == selector:
            selected.append(case)
    if not selected:
        raise ValueError(f"Case not found: {case_selector!r}")
    return selected


def _build_step_payload(step: EvalStep) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    payload.update(dict(step.payload or {}))
    if "user_input" not in payload:
        payload["user_input"] = step.user_input
    if "intent" not in payload and step.intent is not None:
        payload["intent"] = step.intent
    if "character" not in payload and step.character is not None:
        payload["character"] = step.character
    return payload


def _snapshot_telemetry_events(sink: Any) -> List[Dict[str, Any]]:
    events = getattr(sink, "events", None)
    if isinstance(events, list):
        return [dict(item) for item in events if isinstance(item, dict)]
    return []


async def run_eval_case(
    *,
    case: EvalCase,
    options: RunOptions,
) -> Dict[str, Any]:
    run_id = build_run_id(suite=options.suite, case_id=case.session_id)
    paths: EvalArtifactPaths = create_artifact_paths(output_root=options.output_root, run_id=run_id)
    writer = ArtifactWriter(paths)
    telemetry_sink = create_telemetry_sink(paths)
    replay_context = build_replay_context(case)
    session_id = case.session_id
    map_id = str(case.session.get("map_id") or "").strip() or None
    step_reports: List[Dict[str, Any]] = []
    overall_passed = True

    if GameService is None:
        raise RuntimeError(
            "GameService is unavailable because optional runtime dependencies are missing."
        )
    service = GameService(db_path=str(paths.local_db_path))

    with telemetry_scope(telemetry_sink), ExitStack() as stack:
        stack.enter_context(
            apply_replay_patches(
                replay_context,
                llm_specs=_build_eval_llm_specs(),
            )
        )
        stack.enter_context(
            patch(
                "core.graph.nodes.generation.ChatOpenAI",
                new=lambda *args, **kwargs: _ScriptedChatOpenAI(replay_context),
            )
        )
        for index, step in enumerate(case.steps):
            step_payload = _build_step_payload(step)
            step_ok = True
            step_error: str | None = None
            response_payload: Dict[str, Any] = {}
            snapshot_payload: Dict[str, Any] = {}
            assertion_report = AssertionReport()
            try:
                process_kwargs = {
                    "user_input": str(step_payload.pop("user_input", "")),
                    "intent": step_payload.pop("intent", None),
                    "session_id": session_id,
                    "character": step_payload.pop("character", None),
                    "map_id": map_id,
                }
                target = step_payload.pop("target", None)
                source = step_payload.pop("source", None)
                if target is not None:
                    process_kwargs["target"] = target
                if source is not None:
                    process_kwargs["source"] = source
                response_payload = await service.process_chat_turn(
                    **process_kwargs,
                )
                snapshot_payload = await service.get_state_snapshot(
                    session_id=session_id,
                    initialize_if_missing=True,
                    map_id=map_id,
                )
                assertion_report = assert_eval_expectations(
                    expected=step.expected,
                    response=response_payload,
                    state=snapshot_payload,
                    telemetry_summary=telemetry_sink.summary(),
                    telemetry_events=_snapshot_telemetry_events(telemetry_sink),
                )
                step_ok = assertion_report.ok
            except Exception as exc:
                step_ok = False
                step_error = f"{exc.__class__.__name__}: {exc}"
                response_payload = {"error": step_error}
                snapshot_payload = {}
                assertion_report.add(
                    category="runtime_exception",
                    message="Step execution raised exception.",
                    expected="no_exception",
                    actual=step_error,
                )
                assertion_report.add(
                    category="traceback",
                    message=traceback.format_exc(),
                )

            step_record = {
                "index": index,
                "step_id": step.id,
                "request": {
                    "user_input": step.user_input,
                    "intent": step.intent,
                    "character": step.character,
                    "payload": dict(step.payload or {}),
                },
                "response": copy.deepcopy(response_payload),
                "assertions": assertion_report.to_dict(),
                "ok": step_ok,
            }
            writer.append_transcript(step_record)
            step_reports.append(step_record)
            if not step_ok:
                overall_passed = False
                break

        final_state = await service.get_state_snapshot(
            session_id=session_id,
            initialize_if_missing=True,
            map_id=map_id,
        )
        writer.write_final_state(copy.deepcopy(final_state))
        case_assertions = assert_eval_expectations(
            expected=case.expected,
            response={},
            state=final_state,
            telemetry_summary=telemetry_sink.summary(),
            telemetry_events=_snapshot_telemetry_events(telemetry_sink),
        )
        if not case_assertions.ok:
            overall_passed = False
        telemetry_summary = telemetry_sink.summary()

    summary: Dict[str, Any] = {
        "suite": options.suite,
        "case_id": case.session_id,
        "case_source": case.source,
        "run_id": run_id,
        "ok": overall_passed,
        "step_count": len(case.steps),
        "executed_steps": len(step_reports),
        "failed_steps": [item["step_id"] for item in step_reports if not item["ok"]],
        "case_assertions": case_assertions.to_dict(),
        "telemetry": telemetry_summary,
        "artifacts": {
            "run_dir": str(paths.run_dir),
            "transcript": str(paths.transcript_path),
            "telemetry": str(paths.telemetry_path),
            "final_state": str(paths.final_state_path),
            "summary": str(paths.summary_path),
        },
    }
    writer.write_summary(summary)
    return summary


async def run_eval_suite(
    *,
    suite: str,
    eval_dir: str | Path,
    case_selector: str | None,
    output_root: str = "artifacts/evals",
) -> Dict[str, Any]:
    cases = _filter_cases(_load_suite_cases(suite=suite, eval_dir=eval_dir), case_selector)
    if not cases:
        raise ValueError("No eval cases found.")

    options = RunOptions(output_root=output_root, suite=suite)
    results: List[Dict[str, Any]] = []
    for case in cases:
        result = await run_eval_case(case=case, options=options)
        results.append(result)

    passed_count = sum(1 for item in results if bool(item.get("ok")))
    return {
        "suite": suite,
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "ok": passed_count == len(results),
        "results": results,
    }


def run_eval_suite_sync(
    *,
    suite: str,
    eval_dir: str | Path,
    case_selector: str | None,
    output_root: str = "artifacts/evals",
) -> Dict[str, Any]:
    return asyncio.run(
        run_eval_suite(
            suite=suite,
            eval_dir=eval_dir,
            case_selector=case_selector,
            output_root=output_root,
        )
    )


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


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
