from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from core.eval.telemetry import JsonlTelemetrySink


def build_run_id(*, suite: str, case_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    normalized_suite = str(suite or "suite").strip().replace("/", "_")
    normalized_case = str(case_id or "case").strip().replace("/", "_")
    return f"{ts}_{normalized_suite}_{normalized_case}"


@dataclass(frozen=True)
class EvalArtifactPaths:
    run_dir: Path
    transcript_path: Path
    telemetry_path: Path
    final_state_path: Path
    summary_path: Path
    local_db_path: Path


def create_artifact_paths(
    *,
    output_root: str | Path,
    run_id: str,
) -> EvalArtifactPaths:
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return EvalArtifactPaths(
        run_dir=run_dir,
        transcript_path=run_dir / "transcript.jsonl",
        telemetry_path=run_dir / "telemetry.jsonl",
        final_state_path=run_dir / "final_state.json",
        summary_path=run_dir / "summary.json",
        local_db_path=run_dir / "eval_memory.db",
    )


class ArtifactWriter:
    def __init__(self, paths: EvalArtifactPaths) -> None:
        self.paths = paths

    def append_transcript(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        with self.paths.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    def write_final_state(self, payload: Dict[str, Any]) -> None:
        self.write_json(self.paths.final_state_path, payload)

    def write_summary(self, payload: Dict[str, Any]) -> None:
        self.write_json(self.paths.summary_path, payload)


def create_telemetry_sink(paths: EvalArtifactPaths) -> JsonlTelemetrySink:
    return JsonlTelemetrySink(telemetry_path=paths.telemetry_path)
