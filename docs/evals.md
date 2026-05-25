# Evals

Common checks:

```bash
pytest -q
python -m core.eval.runner --suite golden
make check
```

Golden replay cases live under `evals/golden/`. They are deterministic and validate routing, event application, actor visibility, memory isolation, and scenario outcomes.

Real LLM benchmark cases live under `evals/benchmark/` and are manual because they depend on provider latency and credentials.
