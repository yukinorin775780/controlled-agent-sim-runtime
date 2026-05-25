from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, MutableMapping, Protocol


ALLOWED_TELEMETRY_EVENTS = frozenset(
    {
        "turn_started",
        "turn_finished",
        "node_finished",
        "llm_call",
        "memory_retrieval",
        "actor_runtime_decision",
        "event_drain",
        "first_token",
        "benchmark_step",
        "action_result",
        "reflection_enqueued",
        "reflection_processed",
        "client_player_position_ignored",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_token_usage(raw: Mapping[str, Any] | None) -> Dict[str, int]:
    data = dict(raw or {})

    prompt_tokens = _as_int(data.get("prompt_tokens", data.get("input_tokens", 0)))
    completion_tokens = _as_int(
        data.get("completion_tokens", data.get("output_tokens", data.get("generated_tokens", 0)))
    )
    total_tokens = _as_int(data.get("total_tokens", prompt_tokens + completion_tokens))

    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": max(0, total_tokens),
    }


def extract_token_usage(payload: Any) -> Dict[str, int]:
    """
    Extract token usage from common SDK response shapes:
    1) OpenAI response.usage
    2) LangChain usage_metadata / response_metadata
    """
    if payload is None:
        return normalize_token_usage({})

    # OpenAI style: response.usage.{prompt_tokens, completion_tokens, total_tokens}
    usage_obj = _safe_get(payload, "usage")
    if usage_obj is not None:
        usage_dict = _coerce_dict(usage_obj)
        if usage_dict or any(hasattr(usage_obj, name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")):
            usage_dict = usage_dict or {
                "prompt_tokens": _safe_get(usage_obj, "prompt_tokens", 0),
                "completion_tokens": _safe_get(usage_obj, "completion_tokens", 0),
                "total_tokens": _safe_get(usage_obj, "total_tokens", 0),
            }
            return normalize_token_usage(usage_dict)

    # LangChain style: AIMessage.usage_metadata
    usage_metadata = _safe_get(payload, "usage_metadata")
    if isinstance(usage_metadata, Mapping) and usage_metadata:
        return normalize_token_usage(dict(usage_metadata))

    # LangChain style: AIMessage.response_metadata['token_usage' or direct counters]
    response_metadata = _safe_get(payload, "response_metadata")
    if isinstance(response_metadata, Mapping) and response_metadata:
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, Mapping):
            return normalize_token_usage(dict(token_usage))
        return normalize_token_usage(dict(response_metadata))

    return normalize_token_usage({})


class TelemetrySink(Protocol):
    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        ...

    def summary(self) -> Dict[str, Any]:
        ...


@dataclass
class _TelemetryAccumulator:
    total_duration_ms: float = 0.0
    node_durations_ms: Dict[str, float] = None  # type: ignore[assignment]
    actor_runtime_durations_ms: Dict[str, float] = None  # type: ignore[assignment]
    retrieval_hits: int = 0
    token_prompt: int = 0
    token_completion: int = 0
    token_total: int = 0

    def __post_init__(self) -> None:
        if self.node_durations_ms is None:
            self.node_durations_ms = {}
        if self.actor_runtime_durations_ms is None:
            self.actor_runtime_durations_ms = {}

    def ingest(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if event_name == "turn_finished":
            self.total_duration_ms += _as_float(payload.get("duration_ms", 0))
        elif event_name == "node_finished":
            node_name = str(payload.get("node_name") or payload.get("node") or "unknown")
            self.node_durations_ms[node_name] = (
                self.node_durations_ms.get(node_name, 0.0) + _as_float(payload.get("timing_ms", 0))
            )
        elif event_name == "actor_runtime_decision":
            actor_id = str(payload.get("actor_id") or "unknown")
            self.actor_runtime_durations_ms[actor_id] = (
                self.actor_runtime_durations_ms.get(actor_id, 0.0)
                + _as_float(payload.get("duration_ms", 0))
            )
        elif event_name == "memory_retrieval":
            self.retrieval_hits += max(0, _as_int(payload.get("hit_count", 0)))
        elif event_name == "llm_call":
            usage = normalize_token_usage(payload.get("token_usage") if isinstance(payload, Mapping) else {})
            self.token_prompt += usage["prompt_tokens"]
            self.token_completion += usage["completion_tokens"]
            self.token_total += usage["total_tokens"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_duration_ms": int(round(self.total_duration_ms)),
            "node_durations_ms": {k: int(round(v)) for k, v in sorted(self.node_durations_ms.items())},
            "actor_runtime_durations_ms": {
                k: int(round(v)) for k, v in sorted(self.actor_runtime_durations_ms.items())
            },
            "retrieval_hit_count": int(self.retrieval_hits),
            "token_usage": {
                "prompt_tokens": int(self.token_prompt),
                "completion_tokens": int(self.token_completion),
                "total_tokens": int(self.token_total),
            },
        }


class _NoopTelemetrySink:
    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        _ = (event_name, payload)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_duration_ms": 0,
            "node_durations_ms": {},
            "actor_runtime_durations_ms": {},
            "retrieval_hit_count": 0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


class InMemoryTelemetrySink:
    def __init__(self) -> None:
        self._events: List[Dict[str, Any]] = []
        self._acc = _TelemetryAccumulator()
        self._lock = threading.Lock()

    @property
    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        event = {
            "event_name": str(event_name),
            "timestamp": _utc_now_iso(),
            "payload": dict(payload),
        }
        with self._lock:
            self._events.append(event)
            self._acc.ingest(str(event_name), dict(payload))

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return self._acc.as_dict()


class JsonlTelemetrySink:
    def __init__(self, *, telemetry_path: str | Path, flush: bool = True) -> None:
        self._path = Path(telemetry_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._flush = bool(flush)
        self._acc = _TelemetryAccumulator()
        self._events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        event = {
            "event_name": str(event_name),
            "timestamp": _utc_now_iso(),
            "payload": dict(payload),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                if self._flush:
                    handle.flush()
            self._events.append(event)
            self._acc.ingest(str(event_name), dict(payload))

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return self._acc.as_dict()

    def write_summary(self, summary_path: str | Path) -> Path:
        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path


_NOOP_SINK = _NoopTelemetrySink()
_GLOBAL_SINK: TelemetrySink = _NOOP_SINK
_ACTIVE_SINK: ContextVar[TelemetrySink | None] = ContextVar("active_telemetry_sink", default=None)


def set_global_telemetry_sink(sink: TelemetrySink | None) -> None:
    global _GLOBAL_SINK
    _GLOBAL_SINK = sink or _NOOP_SINK


def get_current_telemetry_sink() -> TelemetrySink:
    active = _ACTIVE_SINK.get()
    if active is not None:
        return active
    return _GLOBAL_SINK


@contextmanager
def telemetry_scope(sink: TelemetrySink | None) -> Iterator[TelemetrySink]:
    token = _ACTIVE_SINK.set(sink or _NOOP_SINK)
    try:
        yield get_current_telemetry_sink()
    finally:
        _ACTIVE_SINK.reset(token)


def emit_telemetry(event_name: str, **payload: Any) -> None:
    normalized_event = str(event_name or "").strip()
    if normalized_event not in ALLOWED_TELEMETRY_EVENTS:
        raise ValueError(
            f"Unsupported telemetry event: {normalized_event!r}. "
            f"Allowed: {sorted(ALLOWED_TELEMETRY_EVENTS)}"
        )
    sink = get_current_telemetry_sink()
    sink.emit(normalized_event, payload)
