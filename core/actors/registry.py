from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from core.actors.contracts import ActorRuntime
from core.actors.runtime import TemplateActorRuntime


class ActorRegistry:
    def __init__(self) -> None:
        self._runtimes: Dict[str, ActorRuntime] = {}

    def register(self, actor_id: str, runtime: ActorRuntime) -> None:
        self._runtimes[str(actor_id or "").strip().lower()] = runtime

    def try_get(self, actor_id: str) -> Optional[ActorRuntime]:
        return self._runtimes.get(str(actor_id or "").strip().lower())

    def get(self, actor_id: str) -> ActorRuntime:
        normalized_actor_id = str(actor_id or "").strip().lower()
        runtime = self._runtimes.get(normalized_actor_id)
        if runtime is None:
            raise KeyError(f"Unknown actor runtime: {normalized_actor_id}")
        return runtime


_DEFAULT_ACTOR_REGISTRY: Optional[ActorRegistry] = None
_RUNTIME_ACTORS_FALLBACK: tuple[str, ...] = ("analyst", "scout")


def _normalize_actor_ids(raw_items: Iterable[Any]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        actor_id = str(item or "").strip().lower()
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        out.append(actor_id)
    return tuple(out)


def _default_runtime_registry_config_path() -> Path:
    return Path("config") / "runtime_actor_registry.yaml"


def load_runtime_actor_ids(config_path: str | Path | None = None) -> tuple[str, ...]:
    path = Path(config_path) if config_path is not None else _default_runtime_registry_config_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return _RUNTIME_ACTORS_FALLBACK
    except yaml.YAMLError:
        return _RUNTIME_ACTORS_FALLBACK

    if not isinstance(raw, dict):
        return _RUNTIME_ACTORS_FALLBACK
    actor_items = raw.get("runtime_enabled_actors") or raw.get("runtime_actors") or []
    if not isinstance(actor_items, list):
        return _RUNTIME_ACTORS_FALLBACK
    normalized = _normalize_actor_ids(actor_items)
    return normalized or _RUNTIME_ACTORS_FALLBACK


def _build_default_actor_registry() -> ActorRegistry:
    registry = ActorRegistry()
    for actor_id in load_runtime_actor_ids():
        registry.register(actor_id, TemplateActorRuntime(actor_id))
    return registry


def get_default_actor_registry() -> ActorRegistry:
    global _DEFAULT_ACTOR_REGISTRY
    if _DEFAULT_ACTOR_REGISTRY is None:
        _DEFAULT_ACTOR_REGISTRY = _build_default_actor_registry()
    return _DEFAULT_ACTOR_REGISTRY


def reset_default_actor_registry() -> None:
    global _DEFAULT_ACTOR_REGISTRY
    _DEFAULT_ACTOR_REGISTRY = None
