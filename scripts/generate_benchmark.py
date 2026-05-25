from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from core.eval.assertions import AssertionReport, assert_eval_expectations
from core.eval.init import discover_golden_eval_cases
from core.eval.models import EvalCase, EvalStep
from core.eval.telemetry import JsonlTelemetrySink, emit_telemetry, normalize_token_usage, telemetry_scope
from core.eval.token_estimator import estimate_payload_tokens, estimate_text_tokens


REQUIRED_CONFIG_ERROR = "Real LLM benchmark requires API_KEY, BASE_URL, and MODEL_NAME."
ACTION_INTENTS = {"INTERACT", "MOVE", "ATTACK", "LOOT", "READ", "USE_ITEM"}
ACTION_COMMAND_RE = re.compile(r"^/(give|loot|move|attack|use|read|interact|take)\b", re.IGNORECASE)
NODE_CLASS_ORDER = ["dm_router", "physics", "generation", "actor_runtime", "event_drain", "other"]
REAL_LLM_PROVIDERS = {"openai", "dashscope", "langchain_openai", "legacy_engine"}


@dataclass(frozen=True)
class BenchmarkOptions:
    suite: str = "benchmark"
    eval_dir: str | Path = "evals/benchmark"
    case_selector: str | None = None
    output: str | Path = "BENCHMARK.md"
    artifacts_dir: str | Path = "artifacts/benchmarks"
    max_cases: int | None = None
    fail_on_eval_failure: bool = False
    dry_run: bool = False
    naive_llm_latency_ms: int = 2500
    latency_penalty_ms_per_1k_tokens: float = 150.0
    token_estimator: str = "char_heuristic"
    naive_comparison: bool = True
    estimated_tokens: bool = True


@dataclass
class StepMetric:
    case_id: str
    step_id: str
    ok: bool
    first_update_latency_ms: float | None = None
    ttft_ms: float | None = None
    action_attempt: bool = False
    action_success: bool = False
    action_unknown: bool = False
    error: str | None = None
    duration_ms: float | None = None
    llm_call_count: int = 0
    provider_prompt_tokens: int = 0
    provider_completion_tokens: int = 0
    provider_total_tokens: int = 0
    estimated_prompt_tokens: int | None = None
    estimated_completion_tokens: int | None = None
    estimated_total_tokens: int | None = None
    naive_prompt_tokens: int | None = None
    path_class: str = "unknown"


@dataclass
class CaseMetric:
    case_id: str
    source: str
    ok: bool
    step_count: int
    executed_steps: int
    failed_steps: List[str] = field(default_factory=list)
    error: str | None = None
    case_assertions: Dict[str, Any] = field(default_factory=lambda: AssertionReport().to_dict())


@dataclass
class BenchmarkResult:
    suite: str
    model: str
    timestamp: str
    run_dir: Path
    cases: List[CaseMetric]
    steps: List[StepMetric]
    telemetry_events: List[Dict[str, Any]]
    missing_token_usage_calls: int = 0
    options: BenchmarkOptions = field(default_factory=BenchmarkOptions)

    @property
    def case_count(self) -> int:
        return len(self.cases)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def passed_count(self) -> int:
        return sum(1 for item in self.cases if item.ok)


def percentile(values: Sequence[float | int], p: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if p <= 0:
        return clean[0]
    if p >= 100:
        return clean[-1]
    index = max(0, min(len(clean) - 1, math.ceil((p / 100.0) * len(clean)) - 1))
    return clean[index]


def avg(values: Sequence[float | int]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def classify_node_name(node_name: str) -> str:
    normalized = str(node_name or "").strip().lower()
    if "event_drain" in normalized:
        return "event_drain"
    if "mechanics" in normalized or "physics" in normalized:
        return "physics"
    if "generation" in normalized or "dialogue" in normalized:
        return "generation"
    if "actor" in normalized:
        return "actor_runtime"
    if "dm" in normalized or "intent" in normalized or "input" in normalized:
        return "dm_router"
    return "other"


def _load_suite_cases(*, suite: str, eval_dir: str | Path) -> List[EvalCase]:
    if str(suite).strip().lower() not in {"benchmark", "golden"}:
        raise ValueError(
            f"Unsupported suite: {suite!r}. Currently supports: benchmark, golden."
        )
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


def select_cases(
    *,
    suite: str,
    eval_dir: str | Path,
    case_selector: str | None = None,
    max_cases: int | None = None,
) -> List[EvalCase]:
    cases = _filter_cases(_load_suite_cases(suite=suite, eval_dir=eval_dir), case_selector)
    if max_cases is not None:
        cases = cases[: max(0, int(max_cases))]
    return cases


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


def _payload(event: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(event.get("payload") or {}) if isinstance(event.get("payload"), Mapping) else {}


def case_metric_to_dict(case: CaseMetric) -> Dict[str, Any]:
    return {
        "case_id": case.case_id,
        "ok": bool(case.ok),
        "step_count": int(case.step_count),
        "executed_steps": int(case.executed_steps),
        "failed_steps": list(case.failed_steps),
        "error": str(case.error or ""),
        "case_assertions": dict(case.case_assertions or AssertionReport().to_dict()),
    }


def collect_node_breakdown(events: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float | int | None]]:
    buckets: Dict[str, List[float]] = {key: [] for key in NODE_CLASS_ORDER}
    for event in events:
        if event.get("event_name") != "node_finished":
            continue
        payload = _payload(event)
        node_class = classify_node_name(str(payload.get("node_name") or payload.get("node") or ""))
        try:
            buckets.setdefault(node_class, []).append(float(payload.get("timing_ms", 0)))
        except (TypeError, ValueError):
            buckets.setdefault(node_class, []).append(0.0)

    return {
        node_class: {
            "count": len(values),
            "avg_ms": avg(values),
            "p95_ms": percentile(values, 95),
        }
        for node_class, values in buckets.items()
        if values
    }


def collect_token_economy(
    events: Sequence[Mapping[str, Any]],
    *,
    step_count: int,
) -> Dict[str, float | int | bool]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    llm_call_count = 0
    missing_usage_count = 0
    zero_usage_call_count = 0
    for event in events:
        if event.get("event_name") != "llm_call":
            continue
        llm_call_count += 1
        payload = _payload(event)
        raw_usage = payload.get("token_usage")
        if not isinstance(raw_usage, Mapping):
            missing_usage_count += 1
        provider = str(payload.get("provider") or "").strip().lower()
        usage = normalize_token_usage(raw_usage if isinstance(raw_usage, Mapping) else {})
        if isinstance(raw_usage, Mapping) and usage["total_tokens"] == 0 and provider in REAL_LLM_PROVIDERS:
            zero_usage_call_count += 1
        prompt_tokens += usage["prompt_tokens"]
        completion_tokens += usage["completion_tokens"]
        total_tokens += usage["total_tokens"]

    divisor = max(1, int(step_count))
    return {
        "avg_prompt_tokens": prompt_tokens / divisor,
        "avg_completion_tokens": completion_tokens / divisor,
        "avg_total_tokens": total_tokens / divisor,
        "llm_call_count": llm_call_count,
        "missing_usage_count": missing_usage_count,
        "zero_usage_call_count": zero_usage_call_count,
        "usage_available": total_tokens > 0,
    }


def _llm_usage_from_events(events: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for event in events:
        if event.get("event_name") != "llm_call":
            continue
        usage = normalize_token_usage(_payload(event).get("token_usage") or {})
        usage_totals["prompt_tokens"] += usage["prompt_tokens"]
        usage_totals["completion_tokens"] += usage["completion_tokens"]
        usage_totals["total_tokens"] += usage["total_tokens"]
    return usage_totals


def _turn_duration_from_events(events: Sequence[Mapping[str, Any]], fallback_ms: float) -> float:
    for event in reversed(events):
        if event.get("event_name") != "turn_finished":
            continue
        try:
            return float(_payload(event).get("duration_ms", fallback_ms))
        except (TypeError, ValueError):
            return fallback_ms
    return fallback_ms


def _estimate_optimized_prompt_tokens(
    *,
    step: EvalStep,
    response: Mapping[str, Any],
) -> int:
    scoped_payload = {
        "system_scope": "Controlled Agent graph node prompt with routed state slice, visible actor view, and current input",
        "request": {
            "user_input": step.user_input,
            "intent": step.intent,
            "character": step.character,
            "payload": dict(step.payload or {}),
        },
        "visible_response_context": {
            "responses": response.get("responses", []),
            "latest_roll": response.get("latest_roll", {}),
            "journal_events": list(response.get("journal_events") or [])[-3:],
            "current_location": response.get("current_location", ""),
        },
    }
    return estimate_payload_tokens(scoped_payload)


def _estimate_completion_tokens(response: Mapping[str, Any]) -> int:
    texts: List[str] = []
    for item in list(response.get("responses") or []):
        if isinstance(item, Mapping):
            texts.append(str(item.get("text") or ""))
    texts.extend(str(item) for item in list(response.get("journal_events") or []))
    if response.get("latest_roll"):
        texts.append(json.dumps(response.get("latest_roll"), ensure_ascii=False, default=str))
    return estimate_text_tokens("\n".join(text for text in texts if text))


def _estimate_naive_prompt_tokens(
    *,
    step: EvalStep,
    snapshot: Mapping[str, Any],
) -> int:
    game_state = snapshot.get("game_state") if isinstance(snapshot.get("game_state"), Mapping) else snapshot
    naive_payload = {
        "system_rules": (
            "Monolithic Controlled Agent agent: resolve intent, inspect full game state, handle every entity, "
            "all inventories, flags, actor memory, hidden state, physics rules, JSON action output, "
            "narration, dialogue, and validation in one prompt."
        ),
        "full_game_state": game_state,
        "recent_transcript": snapshot.get("responses", []),
        "current_user_input": step.user_input,
        "current_intent": step.intent,
        "current_payload": dict(step.payload or {}),
    }
    return estimate_payload_tokens(naive_payload)


def classify_step_path(events: Sequence[Mapping[str, Any]]) -> str:
    has_generation_llm = False
    has_actor_runtime = False
    has_physics = False
    has_dm_llm = False
    for event in events:
        payload = _payload(event)
        if event.get("event_name") == "llm_call":
            component = str(payload.get("component") or "").strip().lower()
            if component == "generation":
                has_generation_llm = True
            elif component == "dm":
                has_dm_llm = True
        elif event.get("event_name") == "actor_runtime_decision":
            has_actor_runtime = True
        elif event.get("event_name") == "node_finished":
            if classify_node_name(str(payload.get("node_name") or "")) == "physics":
                has_physics = True

    if has_generation_llm:
        return "Generation LLM"
    if has_actor_runtime:
        return "ActorRuntime"
    if has_physics:
        return "Mechanics-only"
    if has_dm_llm:
        return "DM-only"
    return "Router"


def is_action_attempt(step: EvalStep, payload: Mapping[str, Any] | None = None) -> bool:
    merged = dict(payload or {})
    if step.payload.get("benchmark_action_attempt") is False or merged.get("benchmark_action_attempt") is False:
        return False
    intent = str(merged.get("intent") if merged.get("intent") is not None else step.intent or "").strip().upper()
    if intent in ACTION_INTENTS:
        return True
    if str(merged.get("target") or step.payload.get("target") or "").strip():
        return True
    user_input = str(merged.get("user_input") or step.user_input or "").strip()
    if ACTION_COMMAND_RE.search(user_input):
        return True
    return False


def _contains_physical_action(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"physical_action", "ui_events", "domain_events"} and item:
                return True
            if key_text in {"item_transfers", "hp_changes", "flags_changed"} and item:
                return True
            if _contains_physical_action(item):
                return True
    elif isinstance(value, list):
        return any(_contains_physical_action(item) for item in value)
    return False


def infer_action_outcome(
    *,
    step: EvalStep,
    response: Mapping[str, Any],
    telemetry_events: Sequence[Mapping[str, Any]],
    assertion_report: AssertionReport,
) -> tuple[bool, bool, bool]:
    attempt = is_action_attempt(step)
    if not attempt:
        return False, False, False

    if response.get("error"):
        return True, False, True

    if step.expected and assertion_report.ok:
        return True, True, False

    if _contains_physical_action(response):
        return True, True, False

    if response.get("journal_events"):
        return True, True, False

    for event in telemetry_events:
        payload = _payload(event)
        if event.get("event_name") == "event_drain" and int(payload.get("event_count") or 0) > 0:
            return True, True, False
        if event.get("event_name") == "actor_runtime_decision" and payload.get("physical_action"):
            return True, True, False

    return True, False, True


def collect_action_reliability(steps: Sequence[StepMetric]) -> Dict[str, float | int]:
    attempts = sum(1 for item in steps if item.action_attempt)
    successes = sum(1 for item in steps if item.action_success)
    unknown = sum(1 for item in steps if item.action_unknown)
    return {
        "attempts": attempts,
        "successes": successes,
        "unknown": unknown,
        "success_rate": (successes / attempts * 100.0) if attempts else 0.0,
    }


def collect_coverage_summary(
    events: Sequence[Mapping[str, Any]],
    steps: Sequence[StepMetric],
) -> Dict[str, int]:
    node_breakdown = collect_node_breakdown(events)
    dm_llm_calls = 0
    generation_llm_calls = 0
    for event in events:
        if event.get("event_name") != "llm_call":
            continue
        component = str(_payload(event).get("component") or "").strip().lower()
        if component == "dm":
            dm_llm_calls += 1
        if component == "generation":
            generation_llm_calls += 1
    action = collect_action_reliability(steps)
    return {
        "dm_llm_calls": dm_llm_calls,
        "generation_llm_calls": generation_llm_calls,
        "generation_node_samples": int((node_breakdown.get("generation") or {}).get("count") or 0),
        "physics_node_samples": int((node_breakdown.get("physics") or {}).get("count") or 0),
        "action_attempts": int(action["attempts"]),
    }


def baseline_criteria(result: BenchmarkResult) -> List[Dict[str, Any]]:
    pass_rate = (result.passed_count / result.case_count) if result.case_count else 0.0
    coverage = collect_coverage_summary(result.telemetry_events, result.steps)
    action = collect_action_reliability(result.steps)
    return [
        {
            "criterion": "Eval pass rate",
            "required": ">= 80%",
            "actual": f"{pass_rate * 100:.1f}%",
            "ok": pass_rate >= 0.8,
        },
        {
            "criterion": "DM LLM calls",
            "required": "> 0",
            "actual": coverage["dm_llm_calls"],
            "ok": coverage["dm_llm_calls"] > 0,
        },
        {
            "criterion": "Generation LLM calls",
            "required": "> 0",
            "actual": coverage["generation_llm_calls"],
            "ok": coverage["generation_llm_calls"] > 0,
        },
        {
            "criterion": "Generation node samples",
            "required": "> 0",
            "actual": coverage["generation_node_samples"],
            "ok": coverage["generation_node_samples"] > 0,
        },
        {
            "criterion": "Physics node samples",
            "required": "> 0",
            "actual": coverage["physics_node_samples"],
            "ok": coverage["physics_node_samples"] > 0,
        },
        {
            "criterion": "Action attempts",
            "required": "> 0",
            "actual": coverage["action_attempts"],
            "ok": coverage["action_attempts"] > 0,
        },
        {
            "criterion": "Action success rate",
            "required": ">= 80%",
            "actual": f"{float(action['success_rate']):.1f}%",
            "ok": float(action["success_rate"]) >= 80.0,
        },
    ]


def benchmark_status(result: BenchmarkResult) -> str:
    if all(bool(item["ok"]) for item in baseline_criteria(result)):
        return "Baseline"
    return "Experimental"


def _first_token_latency(events: Sequence[Mapping[str, Any]]) -> float | None:
    for event in events:
        if event.get("event_name") != "first_token":
            continue
        payload = _payload(event)
        for key in ("ttft_ms", "latency_ms", "duration_ms"):
            if key in payload:
                try:
                    return float(payload[key])
                except (TypeError, ValueError):
                    return None
    return None


def _snapshot_telemetry_events(sink: Any) -> List[Dict[str, Any]]:
    events = getattr(sink, "events", None)
    if isinstance(events, list):
        return [dict(item) for item in events if isinstance(item, dict)]
    return []


def _settings_value(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def validate_real_llm_config() -> None:
    if not (_settings_value("API_KEY") and _settings_value("BASE_URL") and _settings_value("MODEL_NAME")):
        raise RuntimeError(REQUIRED_CONFIG_ERROR)


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("real_llm_%Y%m%dT%H%M%SZ")


def _safe_case_name(case: EvalCase) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", case.session_id).strip("_") or "case"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


async def run_benchmark_case(
    *,
    case: EvalCase,
    service: Any,
    sink: JsonlTelemetrySink,
    run_id: str,
    case_artifacts_dir: Path,
) -> tuple[CaseMetric, List[StepMetric]]:
    session_id = f"{case.session_id}__benchmark__{run_id}"
    map_id = str(case.session.get("map_id") or "").strip() or None
    transcript_path = case_artifacts_dir / "transcript.jsonl"
    final_state_path = case_artifacts_dir / "final_state.json"
    step_metrics: List[StepMetric] = []
    failed_steps: List[str] = []

    for index, step in enumerate(case.steps):
        step_payload = _build_step_payload(step)
        before_events_count = len(_snapshot_telemetry_events(sink))
        first_update_at: float | None = None
        started_at = time.perf_counter()
        response_payload: Dict[str, Any] = {}
        snapshot_payload: Dict[str, Any] = {}
        assertion_report = AssertionReport()
        step_ok = False
        step_error: str | None = None

        async def stream_handler(node_name: str, payload: Dict[str, Any]) -> None:
            nonlocal first_update_at
            _ = (node_name, payload)
            if first_update_at is None:
                first_update_at = time.perf_counter()

        try:
            response_payload = await service.process_chat_turn(
                user_input=str(step_payload.pop("user_input", "")),
                intent=step_payload.pop("intent", None),
                session_id=session_id,
                character=step_payload.pop("character", None),
                map_id=map_id,
                target=step_payload.pop("target", None),
                source=step_payload.pop("source", None),
                stream_handler=stream_handler,
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
                telemetry_summary=sink.summary(),
                telemetry_events=_snapshot_telemetry_events(sink),
            )
            step_ok = assertion_report.ok
        except Exception as exc:
            step_ok = False
            step_error = f"{exc.__class__.__name__}: {exc}"
            response_payload = {"error": step_error}
            assertion_report.add(
                category="runtime_exception",
                message="Benchmark step execution raised exception.",
                expected="no_exception",
                actual=step_error,
            )
            assertion_report.add(category="traceback", message=traceback.format_exc())

        after_events = _snapshot_telemetry_events(sink)
        step_events = after_events[before_events_count:]
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        first_update_latency_ms = (
            max(0.0, (first_update_at - started_at) * 1000.0) if first_update_at is not None else None
        )
        ttft_ms = _first_token_latency(step_events)
        usage_totals = _llm_usage_from_events(step_events)
        estimated_prompt_tokens = _estimate_optimized_prompt_tokens(
            step=step,
            response=response_payload,
        )
        estimated_completion_tokens = _estimate_completion_tokens(response_payload)
        estimated_total_tokens = estimated_prompt_tokens + estimated_completion_tokens
        naive_prompt_tokens = _estimate_naive_prompt_tokens(
            step=step,
            snapshot=snapshot_payload,
        )
        action_attempt, action_success, action_unknown = infer_action_outcome(
            step=step,
            response=response_payload,
            telemetry_events=step_events,
            assertion_report=assertion_report,
        )
        metric = StepMetric(
            case_id=case.session_id,
            step_id=step.id,
            ok=step_ok,
            first_update_latency_ms=first_update_latency_ms,
            ttft_ms=ttft_ms,
            action_attempt=action_attempt,
            action_success=action_success,
            action_unknown=action_unknown,
            error=step_error,
            duration_ms=_turn_duration_from_events(step_events, elapsed_ms),
            llm_call_count=sum(1 for event in step_events if event.get("event_name") == "llm_call"),
            provider_prompt_tokens=usage_totals["prompt_tokens"],
            provider_completion_tokens=usage_totals["completion_tokens"],
            provider_total_tokens=usage_totals["total_tokens"],
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
            estimated_total_tokens=estimated_total_tokens,
            naive_prompt_tokens=naive_prompt_tokens,
            path_class=classify_step_path(step_events),
        )
        step_metrics.append(metric)
        if not step_ok:
            failed_steps.append(step.id)

        emit_telemetry(
            "benchmark_step",
            case_id=case.session_id,
            step_id=step.id,
            step_index=index,
            success=step_ok,
            first_update_latency_ms=first_update_latency_ms,
            ttft_ms=ttft_ms,
            action_attempt=action_attempt,
            action_success=action_success,
            action_unknown=action_unknown,
            error=step_error or "",
        )
        if action_attempt:
            emit_telemetry(
                "action_result",
                case_id=case.session_id,
                step_id=step.id,
                success=action_success,
                unknown=action_unknown,
            )

        with transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
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
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    case_assertions = AssertionReport()
    case_error: str | None = None
    try:
        final_state = await service.get_state_snapshot(
            session_id=session_id,
            initialize_if_missing=True,
            map_id=map_id,
        )
        _write_json(final_state_path, final_state)
        if case.expected:
            case_assertions = assert_eval_expectations(
                expected=case.expected,
                response={},
                state=final_state,
                telemetry_summary=sink.summary(),
                telemetry_events=_snapshot_telemetry_events(sink),
            )
    except Exception as exc:
        case_error = f"{exc.__class__.__name__}: {exc}"
        case_assertions.add(category="runtime_exception", message="Final state assertion failed.", actual=case_error)

    case_ok = not failed_steps and case_assertions.ok and case_error is None
    return (
        CaseMetric(
            case_id=case.session_id,
            source=case.source,
            ok=case_ok,
            step_count=len(case.steps),
            executed_steps=len(step_metrics),
            failed_steps=failed_steps,
            error=case_error,
            case_assertions=case_assertions.to_dict(),
        ),
        step_metrics,
    )


async def run_benchmark_suite(options: BenchmarkOptions) -> BenchmarkResult:
    cases = select_cases(
        suite=options.suite,
        eval_dir=options.eval_dir,
        case_selector=options.case_selector,
        max_cases=options.max_cases,
    )
    if not cases:
        raise ValueError("No benchmark cases found.")
    if options.dry_run:
        timestamp = datetime.now(timezone.utc).isoformat()
        return BenchmarkResult(
            suite=options.suite,
            model=_settings_value("MODEL_NAME") or "N/A",
            timestamp=timestamp,
            run_dir=Path(options.artifacts_dir),
            cases=[
                CaseMetric(
                    case_id=case.session_id,
                    source=case.source,
                    ok=True,
                    step_count=len(case.steps),
                    executed_steps=0,
                )
                for case in cases
            ],
            steps=[],
            telemetry_events=[],
            options=options,
        )

    validate_real_llm_config()

    from core.application.game_service import GameService

    run_id = _run_id()
    run_dir = Path(options.artifacts_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sink = JsonlTelemetrySink(telemetry_path=run_dir / "telemetry.jsonl")
    case_metrics: List[CaseMetric] = []
    step_metrics: List[StepMetric] = []

    with telemetry_scope(sink):
        for case in cases:
            case_dir = run_dir / _safe_case_name(case)
            case_dir.mkdir(parents=True, exist_ok=True)
            service = GameService(db_path=str(case_dir / "benchmark.sqlite"))
            case_metric, case_steps = await run_benchmark_case(
                case=case,
                service=service,
                sink=sink,
                run_id=run_id,
                case_artifacts_dir=case_dir,
            )
            case_metrics.append(case_metric)
            step_metrics.extend(case_steps)

    telemetry_events = _snapshot_telemetry_events(sink)
    token_economy = collect_token_economy(telemetry_events, step_count=len(step_metrics))
    timestamp = datetime.now(timezone.utc).isoformat()
    result = BenchmarkResult(
        suite=options.suite,
        model=_settings_value("MODEL_NAME"),
        timestamp=timestamp,
        run_dir=run_dir,
        cases=case_metrics,
        steps=step_metrics,
        telemetry_events=telemetry_events,
        missing_token_usage_calls=int(token_economy["missing_usage_count"]),
        options=options,
    )
    summary = {
        "suite": options.suite,
        "model": _settings_value("MODEL_NAME"),
        "timestamp": timestamp,
        "case_count": len(case_metrics),
        "step_count": len(step_metrics),
        "passed_count": sum(1 for item in case_metrics if item.ok),
        "failed_count": sum(1 for item in case_metrics if not item.ok),
        "case_results": [case_metric_to_dict(item) for item in case_metrics],
        "token_economy": token_economy,
        "estimated_token_economy": estimated_token_economy(result),
        "node_breakdown": collect_node_breakdown(telemetry_events),
        "action_reliability": collect_action_reliability(step_metrics),
        "coverage": collect_coverage_summary(telemetry_events, step_metrics),
        "architecture_comparison": architecture_comparison(result),
        "prompt_budget_comparison": prompt_budget_comparison(result),
        "routing_efficiency": routing_efficiency(result),
        "per_case_details": per_case_details(result),
    }
    _write_json(run_dir / "summary.json", summary)
    return result


def _fmt_ms(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    return str(int(round(float(value))))


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return str(int(value))


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    text_rows = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(str(headers[index])), *(len(row[index]) for row in text_rows)) if text_rows else len(str(headers[index]))
        for index in range(len(headers))
    ]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    header = "| " + " | ".join(str(headers[index]).ljust(widths[index]) for index in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(row[index].ljust(widths[index]) for index in range(len(headers))) + " |"
        for row in text_rows
    ]
    return "\n".join([border, header, border, *body, border])


def _case_failure_summary(case: CaseMetric, *, max_failures: int = 3) -> str:
    parts: List[str] = []
    if case.error:
        parts.append(str(case.error))
    assertions = case.case_assertions if isinstance(case.case_assertions, Mapping) else {}
    failures = list(assertions.get("failures") or []) if isinstance(assertions, Mapping) else []
    for failure in failures[:max_failures]:
        if not isinstance(failure, Mapping):
            continue
        category = str(failure.get("category") or "failure")
        path = str(failure.get("path") or "").strip()
        message = str(failure.get("message") or "").strip()
        label = category if not path else f"{category}:{path}"
        parts.append(f"{label} - {message}" if message else label)
    if len(failures) > max_failures:
        parts.append(f"... {len(failures) - max_failures} more")
    return "; ".join(part for part in parts if part) or "-"


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "-").replace("\n", " ").strip() or "-"
    return text.replace("|", "\\|")


def _case_result_rows(cases: Sequence[CaseMetric]) -> List[str]:
    if not cases:
        return ["| - | - | 0/0 | - | - |"]
    rows: List[str] = []
    for case in cases:
        failed_steps = ", ".join(case.failed_steps) if case.failed_steps else "-"
        rows.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(case.case_id),
                    "yes" if case.ok else "no",
                    f"{case.executed_steps}/{case.step_count}",
                    _markdown_cell(failed_steps),
                    _markdown_cell(_case_failure_summary(case)),
                ]
            )
            + " |"
        )
    return rows


def _baseline_criteria_rows(result: BenchmarkResult) -> List[str]:
    return [
        "| "
        + " | ".join(
            [
                _markdown_cell(item["criterion"]),
                _markdown_cell(item["required"]),
                _markdown_cell(item["actual"]),
                "yes" if item["ok"] else "no",
            ]
        )
        + " |"
        for item in baseline_criteria(result)
    ]


def _architecture_rows(result: BenchmarkResult) -> List[str]:
    comparison = architecture_comparison(result)
    if not comparison:
        return []
    optimized = comparison["optimized"]
    naive = comparison["naive"]
    improvements = comparison["improvements"]
    rows = [
        [
            "LLM Calls / Turn",
            _fmt_num(float(optimized["llm_calls_per_turn"])),
            _fmt_num(float(naive["llm_calls_per_turn"])),
            _fmt_signed_reduction(improvements["llm_calls_per_turn"]),
        ],
        [
            "Avg Turn Latency",
            _fmt_ms(optimized["avg_turn_latency_ms"]),
            _fmt_ms(naive["avg_turn_latency_ms"]),
            _fmt_signed_reduction(improvements["avg_turn_latency_ms"]),
        ],
        [
            "Prompt Tokens / Turn (est.)",
            _fmt_ms(optimized["prompt_tokens_per_turn"]),
            _fmt_ms(naive["prompt_tokens_per_turn"]),
            _fmt_signed_reduction(improvements["prompt_tokens_per_turn"]),
        ],
        [
            "Physics Compute",
            _fmt_ms(optimized["physics_compute_ms"]),
            "N/A",
            "deterministic code path",
        ],
        [
            "Action Success Rate",
            f"{float(optimized['action_success_rate']):.1f}%",
            f"{float(naive['action_success_rate']):.1f}%",
            "same benchmark actions",
        ],
    ]
    return [
        "| " + " | ".join(_markdown_cell(cell) for cell in row) + " |"
        for row in rows
    ]


def _prompt_budget_rows(result: BenchmarkResult) -> List[str]:
    rows = []
    for row in prompt_budget_comparison(result):
        rows.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["case"]),
                    _markdown_cell(_fmt_ms(row["optimized_prompt_tokens"])),
                    _markdown_cell(_fmt_ms(row["naive_prompt_tokens"])),
                    _markdown_cell(_fmt_prompt_reduction(row["reduction_percent"])),
                ]
            )
            + " |"
        )
    return rows


def _routing_rows(result: BenchmarkResult) -> List[str]:
    rows = []
    for path, data in routing_efficiency(result).items():
        rows.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(path),
                    str(data["turns"]),
                    _fmt_ms(data["avg_turn_latency_ms"]),
                    _fmt_ms(data["core_node_latency_ms"]),
                    _fmt_num(float(data["llm_calls_per_turn"])),
                    _fmt_ms(data["prompt_tokens_per_turn"]),
                    _markdown_cell(data["description"]),
                ]
            )
            + " |"
        )
    return rows


def _per_case_detail_rows(result: BenchmarkResult) -> List[str]:
    rows = []
    for row in per_case_details(result):
        rows.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["case"]),
                    _markdown_cell(row["path"]),
                    "yes" if row["ok"] else "no",
                    _fmt_ms(row["turn_ms"]),
                    str(row["llm_calls"]),
                    _fmt_ms(row["optimized_prompt_tokens"]),
                    _fmt_ms(row["naive_prompt_tokens"]),
                    _fmt_prompt_reduction(row["reduction_percent"]),
                    _markdown_cell(row["action"]),
                ]
            )
            + " |"
        )
    return rows


def _token_display(token_economy: Mapping[str, Any], key: str) -> str:
    if not bool(token_economy.get("usage_available", False)):
        return "Unavailable"
    return _fmt_num(float(token_economy[key]))


def _effective_prompt_tokens(step: StepMetric, *, allow_estimated: bool) -> int | None:
    if step.provider_prompt_tokens > 0:
        return int(step.provider_prompt_tokens)
    if allow_estimated and step.estimated_prompt_tokens is not None:
        return int(step.estimated_prompt_tokens)
    return None


def _effective_completion_tokens(step: StepMetric, *, allow_estimated: bool) -> int | None:
    if step.provider_completion_tokens > 0:
        return int(step.provider_completion_tokens)
    if allow_estimated and step.estimated_completion_tokens is not None:
        return int(step.estimated_completion_tokens)
    return None


def _avg_optional(values: Sequence[int | float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _reduction_percent(optimized: int | float | None, naive: int | float | None) -> float | None:
    if optimized is None or naive is None or float(naive) <= 0:
        return None
    return (1.0 - (float(optimized) / float(naive))) * 100.0


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _fmt_signed_reduction(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def _fmt_prompt_reduction(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"-{value:.1f}%"


def estimated_token_economy(result: BenchmarkResult) -> Dict[str, float | None]:
    if not result.options.estimated_tokens:
        return {"avg_prompt_tokens": None, "avg_completion_tokens": None, "avg_total_tokens": None}
    prompt_avg = _avg_optional([step.estimated_prompt_tokens for step in result.steps])
    completion_avg = _avg_optional([step.estimated_completion_tokens for step in result.steps])
    total_avg = _avg_optional([step.estimated_total_tokens for step in result.steps])
    return {
        "avg_prompt_tokens": prompt_avg,
        "avg_completion_tokens": completion_avg,
        "avg_total_tokens": total_avg,
    }


def prompt_budget_comparison(result: BenchmarkResult) -> List[Dict[str, Any]]:
    if not result.options.naive_comparison:
        return []
    rows: List[Dict[str, Any]] = []
    for step in result.steps:
        optimized = _effective_prompt_tokens(step, allow_estimated=result.options.estimated_tokens)
        naive = step.naive_prompt_tokens
        reduction = _reduction_percent(optimized, naive)
        if step.provider_prompt_tokens > 0:
            optimized_source = "provider"
        elif result.options.estimated_tokens and step.estimated_prompt_tokens is not None:
            optimized_source = "estimated"
        else:
            optimized_source = "unavailable"
        rows.append(
            {
                "case": step.case_id,
                "optimized_prompt_tokens": optimized,
                "optimized_source": optimized_source,
                "naive_prompt_tokens": naive,
                "reduction_percent": reduction,
            }
        )
    return rows


def routing_efficiency(result: BenchmarkResult) -> Dict[str, Dict[str, Any]]:
    descriptions = {
        "Mechanics-only": "deterministic movement/interaction",
        "ActorRuntime": "template/runtime companion response",
        "Generation LLM": "rich freeform NPC dialogue",
        "DM-only": "intent routing without downstream actor/generation",
        "Router": "non-LLM graph routing",
    }
    core_node_classes = {
        "Mechanics-only": "physics",
        "ActorRuntime": "actor_runtime",
        "Generation LLM": "generation",
        "DM-only": "dm_router",
        "Router": "other",
    }
    node_breakdown = collect_node_breakdown(result.telemetry_events)
    grouped: Dict[str, List[StepMetric]] = {}
    for step in result.steps:
        grouped.setdefault(step.path_class or "unknown", []).append(step)

    out: Dict[str, Dict[str, Any]] = {}
    for path in ["Mechanics-only", "ActorRuntime", "Generation LLM", "DM-only", "Router"]:
        items = grouped.get(path, [])
        if not items:
            continue
        prompt_avg = _avg_optional(
            [_effective_prompt_tokens(step, allow_estimated=result.options.estimated_tokens) for step in items]
        )
        core_node_class = core_node_classes.get(path)
        core_node_latency = (node_breakdown.get(core_node_class or "") or {}).get("avg_ms")
        out[path] = {
            "turns": len(items),
            "avg_turn_latency_ms": _avg_optional([step.duration_ms for step in items]),
            "core_node_latency_ms": core_node_latency,
            "llm_calls_per_turn": sum(step.llm_call_count for step in items) / max(1, len(items)),
            "prompt_tokens_per_turn": prompt_avg,
            "description": descriptions.get(path, ""),
        }
    return out


def architecture_comparison(result: BenchmarkResult) -> Dict[str, Any]:
    if not result.options.naive_comparison:
        return {}
    node_breakdown = collect_node_breakdown(result.telemetry_events)
    action = collect_action_reliability(result.steps)
    optimized_prompt_avg = _avg_optional(
        [_effective_prompt_tokens(step, allow_estimated=result.options.estimated_tokens) for step in result.steps]
    )
    naive_prompt_avg = _avg_optional([step.naive_prompt_tokens for step in result.steps])
    generation_latency = (node_breakdown.get("generation") or {}).get("avg_ms")
    dm_latency = (node_breakdown.get("dm_router") or {}).get("avg_ms")
    observed_llm_latency = float(generation_latency or dm_latency or result.options.naive_llm_latency_ms)
    prompt_delta = max(0.0, float(naive_prompt_avg or 0.0) - float(optimized_prompt_avg or 0.0))
    prompt_size_penalty_ms = prompt_delta * float(result.options.latency_penalty_ms_per_1k_tokens) / 1000.0
    naive_latency = max(float(result.options.naive_llm_latency_ms), observed_llm_latency) + prompt_size_penalty_ms
    optimized_latency = avg([step.duration_ms for step in result.steps if step.duration_ms is not None])
    optimized_llm_calls = sum(step.llm_call_count for step in result.steps) / max(1, result.step_count)
    return {
        "note": "Naive latency is estimated from observed LLM latency plus prompt-size penalty; it is not a second live LLM run.",
        "optimized": {
            "llm_calls_per_turn": optimized_llm_calls,
            "avg_turn_latency_ms": optimized_latency,
            "prompt_tokens_per_turn": optimized_prompt_avg,
            "physics_compute_ms": (node_breakdown.get("physics") or {}).get("avg_ms"),
            "action_success_rate": action["success_rate"],
        },
        "naive": {
            "llm_calls_per_turn": 1.0,
            "avg_turn_latency_ms": naive_latency,
            "prompt_tokens_per_turn": naive_prompt_avg,
            "physics_compute_ms": None,
            "action_success_rate": action["success_rate"],
            "prompt_size_penalty_ms": prompt_size_penalty_ms,
        },
        "improvements": {
            "llm_calls_per_turn": _reduction_percent(optimized_llm_calls, 1.0),
            "avg_turn_latency_ms": _reduction_percent(optimized_latency, naive_latency),
            "prompt_tokens_per_turn": _reduction_percent(optimized_prompt_avg, naive_prompt_avg),
        },
    }


def per_case_details(result: BenchmarkResult) -> List[Dict[str, Any]]:
    case_by_id = {case.case_id: case for case in result.cases}
    rows: List[Dict[str, Any]] = []
    for step in result.steps:
        optimized = _effective_prompt_tokens(step, allow_estimated=result.options.estimated_tokens)
        naive = step.naive_prompt_tokens if result.options.naive_comparison else None
        action = "none"
        if step.action_attempt:
            if step.action_success:
                action = "success"
            elif step.action_unknown:
                action = "unknown"
            else:
                action = "failed"
        rows.append(
            {
                "case": step.case_id,
                "path": step.path_class,
                "ok": bool(case_by_id.get(step.case_id, CaseMetric(step.case_id, "", False, 0, 0)).ok),
                "turn_ms": step.duration_ms,
                "llm_calls": step.llm_call_count,
                "optimized_prompt_tokens": optimized,
                "naive_prompt_tokens": naive,
                "reduction_percent": _reduction_percent(optimized, naive),
                "action": action,
            }
        )
    return rows


def executive_summary(result: BenchmarkResult) -> List[str]:
    node_breakdown = collect_node_breakdown(result.telemetry_events)
    action = collect_action_reliability(result.steps)
    prompt_rows = prompt_budget_comparison(result)
    avg_reduction = _avg_optional([row.get("reduction_percent") for row in prompt_rows])
    physics_ms = _fmt_ms((node_breakdown.get("physics") or {}).get("avg_ms"))
    generation_ms = _fmt_ms((node_breakdown.get("generation") or {}).get("avg_ms"))
    return [
        f"Physics path averages `{physics_ms} ms`, while generation LLM node averages `{generation_ms} ms`.",
        f"ActorView-scoped prompt budget is estimated to be `{_fmt_percent(avg_reduction)}` lower than full-state prompts.",
        f"Mechanics/action turns completed with `{action['attempts']}` action attempts and `{float(action['success_rate']):.1f}%` success rate.",
        "This benchmark separates deterministic game-state computation from high-latency LLM narration.",
    ]


def render_console_report(result: BenchmarkResult) -> str:
    first_updates = [item.first_update_latency_ms for item in result.steps if item.first_update_latency_ms is not None]
    ttfts = [item.ttft_ms for item in result.steps if item.ttft_ms is not None]
    node_breakdown = collect_node_breakdown(result.telemetry_events)
    token_economy = collect_token_economy(result.telemetry_events, step_count=result.step_count)
    action = collect_action_reliability(result.steps)
    coverage = collect_coverage_summary(result.telemetry_events, result.steps)
    status = benchmark_status(result)

    lines = [
        "Controlled Agent Sim Runtime Real-LLM Benchmark",
        _table(
            ["Field", "Value"],
            [
                ["Model", result.model or "N/A"],
                ["Suite", result.suite],
                ["Cases", result.case_count],
                ["Steps", result.step_count],
                ["Eval Pass", f"{result.passed_count}/{result.case_count}"],
                ["Status", status],
            ],
        ),
    ]
    if status == "Experimental":
        lines.extend(["", "Experimental run: not a formal performance baseline."])
    lines.extend([
        "",
        "Latency",
        _table(
            ["Metric", "Avg ms", "P95 ms", "Count"],
            [
                [
                    "First Graph Node Update (not token TTFT)",
                    _fmt_ms(avg(first_updates)),
                    _fmt_ms(percentile(first_updates, 95)),
                    len(first_updates),
                ],
                ["TTFT (token-level)", _fmt_ms(avg(ttfts)), _fmt_ms(percentile(ttfts, 95)), len(ttfts)],
            ],
        ),
        "",
        "Coverage Summary",
        _table(
            ["Metric", "Value"],
            [
                ["DM LLM calls", coverage["dm_llm_calls"]],
                ["Generation LLM calls", coverage["generation_llm_calls"]],
                ["Physics node samples", coverage["physics_node_samples"]],
                ["Action attempts", coverage["action_attempts"]],
            ],
        ),
        "",
        "Node Breakdown",
        _table(
            ["Node Class", "Avg ms", "P95 ms", "Count"],
            [
                [node_class, _fmt_ms(stats["avg_ms"]), _fmt_ms(stats["p95_ms"]), stats["count"]]
                for node_class, stats in node_breakdown.items()
            ]
            or [["N/A", "N/A", "N/A", 0]],
        ),
        "",
        "Token Economy",
        _table(
            ["Metric", "Value"],
            [
                ["Avg Prompt Tokens", _token_display(token_economy, "avg_prompt_tokens")],
                ["Avg Output Tokens", _token_display(token_economy, "avg_completion_tokens")],
                ["Avg Total Tokens", _token_display(token_economy, "avg_total_tokens")],
            ],
        ),
        "",
        "Baseline Criteria",
        _table(
            ["Criterion", "Required", "Actual", "OK"],
            [
                [item["criterion"], item["required"], item["actual"], "yes" if item["ok"] else "no"]
                for item in baseline_criteria(result)
            ],
        ),
        "",
        "Action Reliability",
        _table(
            ["Metric", "Value"],
            [
                ["Attempts", action["attempts"]],
                ["Successes", action["successes"]],
                ["Unknown", action["unknown"]],
                ["Success Rate", f"{float(action['success_rate']):.1f}%"],
            ],
        ),
        "",
        "Case Results",
        _table(
            ["Case", "OK", "Steps", "Failed Steps", "Error"],
            [
                [
                    case.case_id,
                    "yes" if case.ok else "no",
                    f"{case.executed_steps}/{case.step_count}",
                    ", ".join(case.failed_steps) if case.failed_steps else "-",
                    _case_failure_summary(case),
                ]
                for case in result.cases
            ]
            or [["-", "-", "0/0", "-", "-"]],
        ),
    ])
    return "\n".join(lines)


def render_markdown_report(result: BenchmarkResult) -> str:
    first_updates = [item.first_update_latency_ms for item in result.steps if item.first_update_latency_ms is not None]
    ttfts = [item.ttft_ms for item in result.steps if item.ttft_ms is not None]
    node_breakdown = collect_node_breakdown(result.telemetry_events)
    token_economy = collect_token_economy(result.telemetry_events, step_count=result.step_count)
    estimated_tokens = estimated_token_economy(result)
    action = collect_action_reliability(result.steps)
    coverage = collect_coverage_summary(result.telemetry_events, result.steps)
    status = benchmark_status(result)
    notes = [
        "This is a real LLM benchmark, not the deterministic CI golden replay runner.",
        "Current graph stream is node-update streaming; strict token-level TTFT requires generation LLM astream instrumentation.",
        "Benchmark results can vary by provider, network conditions, and model load.",
        "Provider Usage is real LLM token usage returned by the provider or LangChain metadata.",
    ]
    if result.options.estimated_tokens:
        notes.append(
            "Token counts are provider usage when available; otherwise estimated using deterministic local estimator."
        )
    else:
        notes.append("Estimated token fallback was disabled for this run.")
    if result.options.naive_comparison:
        notes.append(
            "Naive latency is estimated from observed LLM latency plus prompt-size penalty; it is not a second live LLM run."
        )
    notes.append(
        "Turn latency includes graph orchestration and session initialization; core node latency isolates the routed execution path."
    )
    if status == "Experimental":
        notes.append("Status is Experimental, so this run should not be presented as a formal performance baseline.")
    if coverage["generation_llm_calls"] == 0:
        notes.append("Generation LLM path was not covered; this run is not a formal baseline.")
    if not bool(token_economy.get("usage_available", False)):
        notes.append("Provider or LangChain response did not expose token usage metadata for this run.")
    elif int(token_economy["missing_usage_count"]) > 0:
        notes.append("Provider did not return token usage metadata for some calls.")

    node_rows = [
        f"| {node_class} | {_fmt_ms(stats['avg_ms'])} | {_fmt_ms(stats['p95_ms'])} | {stats['count']} |"
        for node_class, stats in node_breakdown.items()
    ]
    if not node_rows:
        node_rows = ["| N/A | N/A | N/A | 0 |"]

    lines = [
        "# Controlled Agent Sim Runtime Real-LLM Benchmark",
        "",
        f"Status: {status}",
        "",
        "## Executive Summary",
        "",
        *(f"- {line}" for line in executive_summary(result)),
        "",
        "## Run Metadata",
        "",
        f"- Timestamp: `{result.timestamp}`",
        f"- Model: `{result.model or 'N/A'}`",
        f"- Suite: `{result.suite}`",
        f"- Cases: `{result.case_count}`",
        f"- Steps: `{result.step_count}`",
        f"- Eval Pass: `{result.passed_count}/{result.case_count}`",
        f"- Artifacts: `{result.run_dir}`",
        "",
        "## Latency",
        "",
        "| Metric | Avg ms | P95 ms | Count |",
        "| --- | ---: | ---: | ---: |",
        f"| First Graph Node Update (not token TTFT) | {_fmt_ms(avg(first_updates))} | {_fmt_ms(percentile(first_updates, 95))} | {len(first_updates)} |",
        f"| TTFT (token-level) | {_fmt_ms(avg(ttfts))} | {_fmt_ms(percentile(ttfts, 95))} | {len(ttfts)} |",
        "",
        "## Coverage Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| DM LLM calls | {coverage['dm_llm_calls']} |",
        f"| Generation LLM calls | {coverage['generation_llm_calls']} |",
        f"| Physics node samples | {coverage['physics_node_samples']} |",
        f"| Action attempts | {coverage['action_attempts']} |",
        "",
        "## Node Breakdown",
        "",
        "| Node Class | Avg ms | P95 ms | Count |",
        "| --- | ---: | ---: | ---: |",
        *node_rows,
        "",
        "## Token Economy",
        "",
        "| Metric | Provider Usage | Estimated |",
        "| --- | ---: | ---: |",
        f"| Avg Prompt Tokens | {_token_display(token_economy, 'avg_prompt_tokens')} | {_fmt_num(estimated_tokens['avg_prompt_tokens'])} |",
        f"| Avg Output Tokens | {_token_display(token_economy, 'avg_completion_tokens')} | {_fmt_num(estimated_tokens['avg_completion_tokens'])} |",
        f"| Avg Total Tokens | {_token_display(token_economy, 'avg_total_tokens')} | {_fmt_num(estimated_tokens['avg_total_tokens'])} |",
        "",
    ]

    if result.options.naive_comparison:
        lines.extend(
            [
                "## Architecture Comparison",
                "",
                "| Metric | Optimized Graph Agent | Naive Monolithic Agent | Improvement |",
                "| --- | ---: | ---: | ---: |",
                *_architecture_rows(result),
                "",
                "## Prompt Budget Comparison (Estimated)",
                "",
                "| Case | Optimized Scoped Prompt Tokens (est.) | Naive Full-State Tokens (est.) | Reduction |",
                "| --- | ---: | ---: | ---: |",
                *(_prompt_budget_rows(result) or ["| - | N/A | N/A | N/A |"]),
                "",
            ]
        )

    lines.extend(
        [
            "## Routing Efficiency",
            "",
            "| Path | Turns | Avg Turn Latency | Core Node Latency | LLM Calls / Turn | Prompt Tokens / Turn | Description |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
            *(_routing_rows(result) or ["| - | 0 | N/A | N/A | N/A | N/A | - |"]),
            "",
            "## Baseline Criteria",
            "",
            "| Criterion | Required | Actual | OK |",
            "| --- | --- | --- | --- |",
            *_baseline_criteria_rows(result),
            "",
            "## Action Success",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Attempts | {action['attempts']} |",
            f"| Successes | {action['successes']} |",
            f"| Unknown | {action['unknown']} |",
            f"| Success Rate | {float(action['success_rate']):.1f}% |",
            "",
            "## Per-Case Details",
            "",
            "| Case | Path | OK | Turn ms | LLM Calls | Optimized Prompt | Naive Prompt | Reduction | Action |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            *(_per_case_detail_rows(result) or ["| - | - | - | N/A | 0 | N/A | N/A | N/A | - |"]),
            "",
            "## Case Results",
            "",
            "| Case | OK | Steps | Failed Steps | Error |",
            "| --- | --- | ---: | --- | --- |",
            *_case_result_rows(result.cases),
            "",
            "## Notes",
            "",
            *(f"- {note}" for note in notes),
            "",
        ]
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a real-LLM benchmark report for controlled-agent-sim-runtime.")
    parser.add_argument(
        "--suite",
        default="benchmark",
        help="Benchmark suite name. Default: benchmark. Golden cases remain supported for manual compatibility.",
    )
    parser.add_argument("--eval-dir", default="evals/benchmark", help="Directory that contains eval case YAML files.")
    parser.add_argument("--case", default=None, help="Optional case selector (session.id or YAML filename stem).")
    parser.add_argument("--output", default="BENCHMARK.md", help="Markdown benchmark output path.")
    parser.add_argument("--artifacts-dir", default="artifacts/benchmarks", help="Benchmark artifacts root.")
    parser.add_argument("--max-cases", type=int, default=None, help="Limit cases to control real LLM cost.")
    parser.add_argument(
        "--naive-llm-latency-ms",
        type=int,
        default=2500,
        help="Default latency estimate for the naive monolithic LLM baseline when no measured LLM node is available.",
    )
    parser.add_argument(
        "--latency-penalty-ms-per-1k-tokens",
        type=float,
        default=150.0,
        help="Prompt-size latency penalty applied to naive full-state baseline per 1k extra prompt tokens.",
    )
    parser.add_argument(
        "--token-estimator",
        default="char_heuristic",
        choices=["char_heuristic"],
        help="Local deterministic token estimator used when provider usage metadata is unavailable.",
    )
    parser.add_argument(
        "--no-naive-comparison",
        action="store_true",
        help="Disable deterministic naive monolithic baseline comparison sections.",
    )
    parser.add_argument(
        "--no-estimated-tokens",
        action="store_true",
        help="Disable local estimated token fallback when provider usage metadata is unavailable.",
    )
    parser.add_argument(
        "--fail-on-eval-failure",
        action="store_true",
        help="Return non-zero when benchmark eval expectations fail.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List selected cases without calling the LLM.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    options = BenchmarkOptions(
        suite=args.suite,
        eval_dir=args.eval_dir,
        case_selector=args.case,
        output=args.output,
        artifacts_dir=args.artifacts_dir,
        max_cases=args.max_cases,
        naive_llm_latency_ms=int(args.naive_llm_latency_ms),
        latency_penalty_ms_per_1k_tokens=float(args.latency_penalty_ms_per_1k_tokens),
        token_estimator=args.token_estimator,
        naive_comparison=not bool(args.no_naive_comparison),
        estimated_tokens=not bool(args.no_estimated_tokens),
        fail_on_eval_failure=bool(args.fail_on_eval_failure),
        dry_run=bool(args.dry_run),
    )
    try:
        result = asyncio.run(run_benchmark_suite(options))
    except RuntimeError as exc:
        if str(exc) == REQUIRED_CONFIG_ERROR:
            print(REQUIRED_CONFIG_ERROR, file=sys.stderr)
            return 2
        print(f"[Benchmark] failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[Benchmark] failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if options.dry_run:
        print("Dry run: selected benchmark cases")
        for case in result.cases:
            print(f"- {case.case_id} ({case.step_count} steps)")
        return 0

    output_path = Path(options.output)
    output_path.write_text(render_markdown_report(result), encoding="utf-8")
    print(render_console_report(result))
    print(f"\nMarkdown report: {output_path}")
    print(f"Artifacts: {result.run_dir}")

    if options.fail_on_eval_failure and result.passed_count != result.case_count:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
