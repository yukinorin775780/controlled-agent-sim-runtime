from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence
from unittest.mock import patch

from core.eval.models import ReplayContext


@dataclass(frozen=True)
class LlmPatchSpec:
    target: str
    channel: str = "default"
    is_async: bool = False


class _ClockDateTimeProxy:
    def __init__(self, ctx: ReplayContext) -> None:
        self._ctx = ctx

    def now(self, tz: Any = None):  # noqa: ANN201 - keep datetime compatibility
        return self._ctx.clock.now(tz=tz)


def default_llm_patch_specs() -> tuple[LlmPatchSpec, ...]:
    """
    Default scripted-LLM patch targets.
    Kept as optional overlays so replay layer stays independent from business internals.
    """
    return (
        LlmPatchSpec(target="core.llm.dm.analyze_intent", channel="dm", is_async=False),
        LlmPatchSpec(target="core.engine.generate_dialogue", channel="generation", is_async=False),
    )


def replay_patch_targets() -> tuple[str, ...]:
    """
    Determinism patch targets for eval replay.
    """
    return (
        "core.application.game_service.time.perf_counter",
        "core.memory.distiller.datetime",
        "core.memory.compat.datetime",
        "core.systems.dice.random.randint",
        "core.systems.mechanics.random.randint",
        "core.graph.nodes.dm.random.choice",
        "core.graph.graph_routers.random.random",
    )


def apply_replay_patches(
    ctx: ReplayContext,
    *,
    llm_specs: Sequence[LlmPatchSpec] | None = None,
) -> AbstractContextManager[ReplayContext]:
    stack = ExitStack()
    datetime_proxy = _ClockDateTimeProxy(ctx)

    # Core deterministic runtime patches.
    stack.enter_context(
        patch("core.application.game_service.time.perf_counter", new=ctx.clock.perf_counter)
    )
    stack.enter_context(
        patch("core.memory.distiller.datetime", new=datetime_proxy)
    )
    stack.enter_context(
        patch("core.memory.compat.datetime", new=datetime_proxy)
    )
    stack.enter_context(
        patch("core.systems.dice.random.randint", new=ctx.rng.randint)
    )
    stack.enter_context(
        patch("core.systems.mechanics.random.randint", new=ctx.rng.randint)
    )
    stack.enter_context(
        patch("core.graph.nodes.dm.random.choice", new=ctx.rng.choice)
    )
    stack.enter_context(
        patch("core.graph.graph_routers.random.random", new=ctx.rng.random)
    )

    # Optional scripted-LLM patches.
    for spec in list(llm_specs or []):
        if spec.is_async:
            stack.enter_context(
                patch(spec.target, new=ctx.llm.as_async_callable(channel=spec.channel))
            )
        else:
            stack.enter_context(
                patch(spec.target, new=ctx.llm.as_sync_callable(channel=spec.channel))
            )

    class _ReplayContextManager(AbstractContextManager[ReplayContext]):
        def __enter__(self) -> ReplayContext:
            stack.__enter__()
            return ctx

        def __exit__(self, exc_type, exc, tb) -> bool:
            return bool(stack.__exit__(exc_type, exc, tb))

    return _ReplayContextManager()


def iter_default_replay_patches(
    ctx: ReplayContext,
    *,
    with_default_llm: bool = False,
) -> Iterator[AbstractContextManager[ReplayContext]]:
    llm_specs: Iterable[LlmPatchSpec] = default_llm_patch_specs() if with_default_llm else ()
    yield apply_replay_patches(ctx, llm_specs=tuple(llm_specs))
