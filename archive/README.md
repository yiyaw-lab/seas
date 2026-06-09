# Archive

Superseded SEAS generations, preserved for history without cluttering the live
system. **Nothing in `archive/` is part of the live runtime** — these modules are
not imported by any current entrypoint, workflow, or test. The dead set was
computed by tracing the import closure from the real entrypoints (`seas.py`,
`seas_finding.py`, `seas_benchmark.py`, the `argo_*` runtime, `fetch_signals.py`)
plus the test suite; see [`docs/audits/AUDIT.md`](../docs/audits/AUDIT.md) for the
generational history.

The live architecture is in the top-level [README](../README.md):

```
Signal → score → rank → topical sources → synthesis → EMISSION GATE → finding | probe
```

## What moved here (`src-legacy/`, 24 modules)

**Gen-1 manual scoring / signal intake** (superseded by `fetch_signals.py` +
LLM auto-scoring in `seas_finding.py`):
`add_signal`, `score_signals`, `import_scores`, `apply_score`, `auto_score`,
`enrich_signal`, `apply_enrichment`, `classify_signal`

**Gen-2 capability engine** (concept removed from the north star):
`frontier_brief`, `capability_inventory`, `capability_analysis_job`,
`ingest_capability_analysis`, `add_capability`, `link_experiment`,
`link_artifact`, `update_capability_status`, `recommend_next_capability`

**Gen-2 experiment flow** (superseded by the V3 gate-based `seas_finding.py`):
`generate_experiment_job`, `opportunity_job`, `build_plan`, `choose_experiment`,
`experiment`, `main`, `week`

## Deleted (not archived)

Two empty 0-line stubs were removed outright: `ledger.py`, `scan.py`.

## Note on `score.py`

`score.py` is **not** here — it is live. The V1→V3 wiring commit made
`opportunities.py` (called by `seas.py`) depend on `score.score_signal` /
`score.qualifies`, so it stays in `src/`.
