from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _resolve_path(data: Any, path: str) -> tuple[bool, Any]:
    current = data
    for part in [p for p in str(path or "").split(".") if p]:
        if isinstance(current, Mapping):
            if part not in current:
                return False, None
            current = current[part]
            continue
        if isinstance(current, list) and _is_int_like(part):
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return False, None
            current = current[idx]
            continue
        return False, None
    return True, current


@dataclass(frozen=True)
class AssertionFailure:
    category: str
    message: str
    path: str = ""
    expected: Any = None
    actual: Any = None


@dataclass
class AssertionReport:
    failures: List[AssertionFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def add(
        self,
        *,
        category: str,
        message: str,
        path: str = "",
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        self.failures.append(
            AssertionFailure(
                category=category,
                message=message,
                path=path,
                expected=expected,
                actual=actual,
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "failure_count": len(self.failures),
            "failures": [
                {
                    "category": item.category,
                    "message": item.message,
                    "path": item.path,
                    "expected": item.expected,
                    "actual": item.actual,
                }
                for item in self.failures
            ],
        }


def _assert_responses(
    expected: Mapping[str, Any],
    response: Mapping[str, Any],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("responses") or {})
    if not exp:
        return

    actual_responses = list(response.get("responses") or [])
    actual_texts = [str(item.get("text") or "") for item in actual_responses if isinstance(item, Mapping)]

    contains = list(exp.get("contains") or [])
    for idx, needle in enumerate(contains):
        token = str(needle)
        if not any(token in text for text in actual_texts):
            report.add(
                category="responses_contains",
                message=f"Response does not contain expected token: {token!r}",
                path=f"responses.contains.{idx}",
                expected=token,
                actual=actual_texts,
            )

    not_contains = list(exp.get("not_contains") or [])
    for idx, forbidden in enumerate(not_contains):
        token = str(forbidden)
        if any(token in text for text in actual_texts):
            report.add(
                category="responses_not_contains",
                message=f"Response contains forbidden token: {token!r}",
                path=f"responses.not_contains.{idx}",
                expected=f"not_contains({token!r})",
                actual=actual_texts,
            )

    if "exact" in exp:
        exact = list(exp.get("exact") or [])
        if actual_responses != exact:
            report.add(
                category="responses_exact",
                message="Response list does not exactly match expected payload.",
                path="responses.exact",
                expected=exact,
                actual=actual_responses,
            )


def _event_matches(event: Mapping[str, Any], matcher: Any) -> bool:
    if isinstance(matcher, str):
        return str(event.get("event_name") or "") == matcher

    if not isinstance(matcher, Mapping):
        return False

    expected_event_name = str(matcher.get("event_name") or "").strip()
    if expected_event_name and str(event.get("event_name") or "") != expected_event_name:
        return False

    expected_payload = matcher.get("payload")
    if isinstance(expected_payload, Mapping):
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return False
        for key, expected_value in expected_payload.items():
            if payload.get(key) != expected_value:
                return False
    return True


def _assert_telemetry(
    expected: Mapping[str, Any],
    telemetry_events: Sequence[Mapping[str, Any]],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("telemetry") or {})
    if not exp:
        return

    contains = list(exp.get("events_contains") or [])
    actual_event_names = [str(event.get("event_name") or "") for event in telemetry_events]
    for idx, matcher in enumerate(contains):
        if not any(_event_matches(event, matcher) for event in telemetry_events):
            report.add(
                category="telemetry_events_contains",
                message="Expected telemetry event was not found.",
                path=f"telemetry.events_contains.{idx}",
                expected=matcher,
                actual=actual_event_names,
            )

    not_contains = list(exp.get("events_not_contains") or [])
    for idx, matcher in enumerate(not_contains):
        if any(_event_matches(event, matcher) for event in telemetry_events):
            report.add(
                category="telemetry_events_not_contains",
                message="Forbidden telemetry event was found.",
                path=f"telemetry.events_not_contains.{idx}",
                expected=matcher,
                actual=actual_event_names,
            )


def _assert_state(
    expected: Mapping[str, Any],
    state: Mapping[str, Any],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("state") or {})
    if not exp:
        return

    equals = dict(exp.get("equals") or {})
    for path, expected_value in equals.items():
        exists, actual_value = _resolve_path(state, str(path))
        if not exists:
            report.add(
                category="state_equals",
                message="Expected state path does not exist.",
                path=str(path),
                expected=expected_value,
                actual=None,
            )
            continue
        if actual_value != expected_value:
            report.add(
                category="state_equals",
                message="State value mismatch.",
                path=str(path),
                expected=expected_value,
                actual=actual_value,
            )

    contains = dict(exp.get("contains") or {})
    for path, expected_items in contains.items():
        exists, actual_value = _resolve_path(state, str(path))
        if not exists:
            report.add(
                category="state_contains",
                message="Expected collection path does not exist.",
                path=str(path),
                expected=expected_items,
                actual=None,
            )
            continue
        expected_list = list(expected_items or [])
        if isinstance(actual_value, str):
            missing = [item for item in expected_list if str(item) not in actual_value]
        elif isinstance(actual_value, Sequence):
            missing = [item for item in expected_list if item not in actual_value]
        else:
            missing = expected_list
        if missing:
            report.add(
                category="state_contains",
                message="Collection missing expected items.",
                path=str(path),
                expected=expected_list,
                actual=actual_value,
            )


def _assert_visibility(
    expected: Mapping[str, Any],
    state: Mapping[str, Any],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("visibility") or {})
    if not exp:
        return

    forbidden = list(exp.get("forbidden_paths") or [])
    for path in forbidden:
        exists, value = _resolve_path(state, str(path))
        if exists:
            report.add(
                category="visibility_forbidden",
                message="Forbidden path is visible.",
                path=str(path),
                expected="not_visible",
                actual=value,
            )

    required = list(exp.get("required_paths") or [])
    for path in required:
        exists, _ = _resolve_path(state, str(path))
        if not exists:
            report.add(
                category="visibility_required",
                message="Required visible path is missing.",
                path=str(path),
                expected="visible",
                actual=None,
            )


def _assert_retrieval(
    expected: Mapping[str, Any],
    telemetry_summary: Mapping[str, Any],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("retrieval") or {})
    if not exp:
        return

    hits = int(telemetry_summary.get("retrieval_hit_count") or 0)
    min_hits = exp.get("min_hits")
    max_hits = exp.get("max_hits")
    if min_hits is not None and hits < int(min_hits):
        report.add(
            category="retrieval_min_hits",
            message="Retrieval hit count is lower than expected.",
            path="retrieval.min_hits",
            expected=int(min_hits),
            actual=hits,
        )
    if max_hits is not None and hits > int(max_hits):
        report.add(
            category="retrieval_max_hits",
            message="Retrieval hit count exceeds budget.",
            path="retrieval.max_hits",
            expected=int(max_hits),
            actual=hits,
        )


def _assert_budget(
    expected: Mapping[str, Any],
    telemetry_summary: Mapping[str, Any],
    report: AssertionReport,
) -> None:
    exp = dict(expected.get("budget") or {})
    if not exp:
        return

    total_duration = float(telemetry_summary.get("total_duration_ms") or 0)
    token_usage = dict(telemetry_summary.get("token_usage") or {})
    total_tokens = int(token_usage.get("total_tokens") or 0)

    max_latency = exp.get("max_latency_ms")
    if max_latency is not None and total_duration > float(max_latency):
        report.add(
            category="budget_latency",
            message="Total latency exceeds max budget.",
            path="budget.max_latency_ms",
            expected=float(max_latency),
            actual=total_duration,
        )

    max_tokens = exp.get("max_total_tokens")
    if max_tokens is not None and total_tokens > int(max_tokens):
        report.add(
            category="budget_tokens",
            message="Total token usage exceeds max budget.",
            path="budget.max_total_tokens",
            expected=int(max_tokens),
            actual=total_tokens,
        )


def assert_eval_expectations(
    *,
    expected: Mapping[str, Any] | None,
    response: Mapping[str, Any],
    state: Mapping[str, Any],
    telemetry_summary: Mapping[str, Any],
    telemetry_events: Sequence[Mapping[str, Any]] | None = None,
) -> AssertionReport:
    report = AssertionReport()
    exp = dict(expected or {})
    if not exp:
        return report

    normalized_telemetry_events = list(telemetry_events or [])
    _assert_responses(exp, response, report)
    _assert_telemetry(exp, normalized_telemetry_events, report)
    _assert_state(exp, state, report)
    _assert_visibility(exp, state, report)
    _assert_retrieval(exp, telemetry_summary, report)
    _assert_budget(exp, telemetry_summary, report)
    return report
