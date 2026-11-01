# Root-cause eval

How well does the analysis find the real cause? This folder holds the data and results that answer that.

- `datasets/<scenario>/<id>.json`: **snapshots** recorded by chaos runs. Each one holds what the analysis saw for one real incident (the alerts, the evidence, which sources were unavailable) and the known cause from the scenario file in `chaos/scenarios/`. They are real recordings, never edited by hand.
- `baseline.json`: the scores the CI gate compares against.

## Run it

```bash
make eval                                     # fake model: checks the plumbing, scores are meaningless
make eval EVAL_ARGS="--provider gemini"       # the real model (needs GEMINI_API_KEY)
```

The report (Markdown and JSON) lands in `evals/reports/latest/`. It shows top-1 and top-3 accuracy (service and category both right), service-only accuracy, how many citations pointed at real evidence, confidence when right versus wrong, latency and tokens, per scenario.

## Record more snapshots

1. Run the `Chaos run` workflow by hand with `scenarios: all`, `repeat: 2`, `llm: fake`, `forward: false` (fake saves free-tier quota; the eval calls the real model later).
2. Collect the snapshots it printed: `GH_TOKEN=... python scripts/collect_snapshots.py --run-id <run id>`.
3. Commit them in a PR.

A scenario that never fires an alert produces no snapshot; that shows as a failed chaos job, and the scenario is fixed or removed.

## Scenarios tried and dropped

These demo failures never fired an alert under the current rules (error ratio above 20%, or p95 latency above 1.5 s, each for a minute), so they produce no incident to analyze:

- `cartFailure` (only fails `EmptyCart`), `adFailure`, `adHighCpu`, `productCatalogFailure` (one product), `recommendationCacheFailure`, `emailMemoryLeak`.

Catching these would need more sensitive rules (for example, error rate well above a service's own normal level), which risks false alerts on the demo's background errors. A possible later improvement.
