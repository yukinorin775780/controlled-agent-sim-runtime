# Controlled Agent Sim Runtime Benchmark

Status: baseline from the real-LLM benchmark harness.

## Executive Summary

- Deterministic physics/action paths average around `1 ms`.
- Real LLM generation is isolated to high-value turns and averaged `1461 ms` in the captured baseline.
- ActorView-scoped prompt construction reduced estimated prompt context by `95.7%` compared with a naive full-state prompt.
- The benchmark separates deterministic simulation state computation from high-latency LLM expression.

## Architecture Comparison

| Metric | Optimized Graph Agent | Naive Full-State Agent | Improvement |
| --- | ---: | ---: | ---: |
| LLM Calls / Turn | 1 | 1 | +0.0% |
| Avg Turn Latency | 1486 ms | 3067 ms | +51.5% |
| Prompt Tokens / Turn (est.) | 172 | 3950 | +95.7% |
| Physics Compute | 1 ms | N/A | deterministic path |
| Action Success Rate | 100.0% | 100.0% | same benchmark actions |

## Notes

- Golden replay is deterministic and suitable for CI.
- Real LLM benchmark runs are manual because provider latency, token accounting, and network behavior vary.
- Token counts are provider usage when available; otherwise the local deterministic estimator is used.
