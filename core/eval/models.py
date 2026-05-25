from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Mapping, MutableMapping, Optional, Sequence


EVAL_CASE_YAML_SCHEMA: Dict[str, Any] = {
    "required": ["session", "determinism", "steps", "expected"],
    "session": {
        "required": ["id"],
        "optional": ["map_id", "seed", "metadata"],
    },
    "determinism": {
        "optional": [
            "perf_counter",
            "now_iso",
            "randint",
            "choice_indices",
            "random_values",
            "llm",
            "strict",
        ],
    },
    "steps": {
        "type": "list",
        "item_required": ["id"],
        "item_optional": ["user_input", "intent", "character", "payload", "expected", "note"],
    },
    "expected": {
        "type": "mapping",
    },
}


@dataclass(frozen=True)
class EvalStep:
    id: str
    user_input: str = ""
    intent: Optional[str] = None
    character: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    expected: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalStep":
        step_id = str(raw.get("id") or "").strip()
        if not step_id:
            raise ValueError("EvalStep.id is required")
        return cls(
            id=step_id,
            user_input=str(raw.get("user_input") or ""),
            intent=(str(raw.get("intent")).strip() if raw.get("intent") is not None else None),
            character=(str(raw.get("character")).strip() if raw.get("character") is not None else None),
            payload=dict(raw.get("payload") or {}),
            expected=dict(raw.get("expected") or {}),
            note=str(raw.get("note") or ""),
        )


@dataclass(frozen=True)
class EvalDeterminism:
    perf_counter: List[float] = field(default_factory=list)
    now_iso: List[str] = field(default_factory=list)
    randint: List[int] = field(default_factory=list)
    choice_indices: List[int] = field(default_factory=list)
    random_values: List[float] = field(default_factory=list)
    llm: Dict[str, List[Any]] = field(default_factory=dict)
    strict: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalDeterminism":
        llm_script_raw = raw.get("llm") or {}
        llm_script: Dict[str, List[Any]] = {}
        if isinstance(llm_script_raw, Mapping):
            for key, values in llm_script_raw.items():
                llm_script[str(key)] = list(values or [])
        return cls(
            perf_counter=[float(item) for item in (raw.get("perf_counter") or [])],
            now_iso=[str(item) for item in (raw.get("now_iso") or [])],
            randint=[int(item) for item in (raw.get("randint") or [])],
            choice_indices=[int(item) for item in (raw.get("choice_indices") or [])],
            random_values=[float(item) for item in (raw.get("random_values") or [])],
            llm=llm_script,
            strict=bool(raw.get("strict", True)),
        )


@dataclass(frozen=True)
class EvalCase:
    session: Dict[str, Any]
    determinism: EvalDeterminism
    steps: List[EvalStep]
    expected: Dict[str, Any]
    source: str = ""

    @property
    def session_id(self) -> str:
        return str(self.session.get("id") or "").strip()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source: str = "") -> "EvalCase":
        session = dict(raw.get("session") or {})
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            raise ValueError("EvalCase.session.id is required")

        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError("EvalCase.steps must be a non-empty list")

        steps = [EvalStep.from_dict(item if isinstance(item, Mapping) else {}) for item in steps_raw]
        determinism = EvalDeterminism.from_dict(raw.get("determinism") or {})
        expected = dict(raw.get("expected") or {})
        return cls(
            session=session,
            determinism=determinism,
            steps=steps,
            expected=expected,
            source=source,
        )


class FakeClock:
    """
    Script-driven clock for deterministic replay.
    - perf_counter(): returns scripted float values in sequence.
    - now(): returns scripted datetimes in sequence.
    """

    def __init__(
        self,
        *,
        perf_counter_script: Optional[Sequence[float]] = None,
        now_iso_script: Optional[Sequence[str]] = None,
        strict: bool = True,
    ) -> None:
        self._perf: Deque[float] = deque(float(v) for v in (perf_counter_script or []))
        self._now: Deque[datetime] = deque(self._parse_datetime(v) for v in (now_iso_script or []))
        self._strict = bool(strict)
        self._last_perf = 0.0
        self._last_now: Optional[datetime] = None

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Invalid datetime script value: empty string")
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def perf_counter(self) -> float:
        if self._perf:
            self._last_perf = self._perf.popleft()
            return self._last_perf
        if self._strict:
            raise RuntimeError("FakeClock.perf_counter script exhausted")
        self._last_perf += 0.001
        return self._last_perf

    def now(self, tz: Any = None) -> datetime:
        if self._now:
            current = self._now.popleft()
            self._last_now = current
        elif self._strict:
            raise RuntimeError("FakeClock.now script exhausted")
        else:
            base = self._last_now or datetime(2026, 1, 1, tzinfo=timezone.utc)
            current = base + timedelta(milliseconds=1)
            self._last_now = current

        if tz is None:
            return current
        try:
            return current.astimezone(tz)
        except Exception:
            return current


class ScriptedRng:
    """
    Deterministic replacement for randint / choice / random.
    """

    def __init__(
        self,
        *,
        randint_script: Optional[Sequence[int]] = None,
        choice_indices_script: Optional[Sequence[int]] = None,
        random_values_script: Optional[Sequence[float]] = None,
        strict: bool = True,
    ) -> None:
        self._randint: Deque[int] = deque(int(v) for v in (randint_script or []))
        self._choice_indices: Deque[int] = deque(int(v) for v in (choice_indices_script or []))
        self._random_values: Deque[float] = deque(float(v) for v in (random_values_script or []))
        self._strict = bool(strict)

    def randint(self, start: int, end: int) -> int:
        low, high = int(start), int(end)
        if low > high:
            low, high = high, low
        if self._randint:
            value = self._randint.popleft()
        elif self._strict:
            raise RuntimeError("ScriptedRng.randint script exhausted")
        else:
            value = low
        if value < low or value > high:
            if self._strict:
                raise ValueError(f"Scripted randint value {value} out of range [{low}, {high}]")
            value = max(low, min(high, value))
        return value

    def choice(self, seq: Sequence[Any]) -> Any:
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        if self._choice_indices:
            idx = self._choice_indices.popleft()
        elif self._strict:
            raise RuntimeError("ScriptedRng.choice script exhausted")
        else:
            idx = 0
        return seq[idx % len(seq)]

    def random(self) -> float:
        if self._random_values:
            value = self._random_values.popleft()
        elif self._strict:
            raise RuntimeError("ScriptedRng.random script exhausted")
        else:
            value = 0.5
        if value < 0.0 or value > 1.0:
            if self._strict:
                raise ValueError(f"Scripted random value {value} out of range [0.0, 1.0]")
            value = max(0.0, min(1.0, value))
        return value


class ScriptedLlm:
    """
    LLM replay interface (transport-agnostic).
    It returns script payloads only and does not expose OpenAI/LangChain structures.
    """

    def __init__(self, script: Optional[Mapping[str, Sequence[Any]]] = None, *, strict: bool = True) -> None:
        normalized: Dict[str, Deque[Any]] = {}
        for key, values in dict(script or {}).items():
            normalized[str(key)] = deque(copy.deepcopy(list(values or [])))
        if "default" not in normalized:
            normalized["default"] = deque()
        self._scripts = normalized
        self._strict = bool(strict)

    def _pop(self, channel: str) -> Any:
        selected = str(channel or "default")
        queue = self._scripts.get(selected)
        if queue is None:
            queue = self._scripts["default"]
        if queue:
            return copy.deepcopy(queue.popleft())
        if self._strict:
            raise RuntimeError(f"ScriptedLlm script exhausted for channel={selected!r}")
        return {}

    def respond(self, *, channel: str = "default", request: Optional[MutableMapping[str, Any]] = None) -> Any:
        _ = request  # request is accepted for compatibility, but replay payload is fully scripted.
        return self._pop(channel)

    async def arespond(
        self,
        *,
        channel: str = "default",
        request: Optional[MutableMapping[str, Any]] = None,
    ) -> Any:
        return self.respond(channel=channel, request=request)

    def as_sync_callable(self, *, channel: str = "default"):
        def _callable(*_args: Any, **_kwargs: Any) -> Any:
            return self.respond(channel=channel)

        return _callable

    def as_async_callable(self, *, channel: str = "default"):
        async def _callable(*_args: Any, **_kwargs: Any) -> Any:
            return await self.arespond(channel=channel)

        return _callable


@dataclass
class ReplayContext:
    case: EvalCase
    clock: FakeClock
    rng: ScriptedRng
    llm: ScriptedLlm

    @classmethod
    def from_case(cls, case: EvalCase) -> "ReplayContext":
        det = case.determinism
        return cls(
            case=case,
            clock=FakeClock(
                perf_counter_script=det.perf_counter,
                now_iso_script=det.now_iso,
                strict=det.strict,
            ),
            rng=ScriptedRng(
                randint_script=det.randint,
                choice_indices_script=det.choice_indices,
                random_values_script=det.random_values,
                strict=det.strict,
            ),
            llm=ScriptedLlm(
                script=det.llm,
                strict=det.strict,
            ),
        )
