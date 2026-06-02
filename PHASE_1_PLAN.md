# SEAS Phase 1 Plan — Quarantine Dead Architecture

Date: 2026-06-02
Status: **PLAN ONLY** — no files moved, deleted, or rewritten. No commits.

North star assumed correct:

```
Signal → Opportunity → Experiment → Finding → Theory
```

## Goal

Quarantine dead and superseded architecture **without breaking the runnable system**.

## Classification Legend

- **Keep** — part of the live runtime, OR conceptually aligned with the north star and actively useful as a reference/source of truth. Stays exactly where it is in Phase 1.
- **Legacy** — aligned with the north star (correct concept) but not yet wired into code, or a hand-authored research artifact. Keep in place; do not archive. Will be formalized in a later phase.
- **Archive** — belongs to a superseded generation (capability engine, manual scoring, Gen-2 experiment flow) or is a stale dated run log. Move to `archive/` in Phase 1. Not deleted.
- **Delete Later** — genuinely worthless (empty stubs, build artifacts). Flagged now, deleted in a later phase after one green run confirms nothing depends on them.

## The Live Runtime (must not break)

Confirmed by tracing imports + `subprocess.run`:

```
seas.py
 ├─ python src/week.py            (reads data/signals.json)
 ├─ python src/opportunities.py   (from score import …; writes data/opportunities.json)
 │    └─ score.py
 └─ python src/main.py            (from experiment import …; writes runs/<date>-experiment.md)
      └─ experiment.py
```

Plus the GitHub Action `seas-weekly.yml`, which calls `python src/seas.py` and `git add data/ runs/`.

**Hard constraint:** these 6 Python files, `data/signals.json`, `data/opportunities.json`, the workflow, and the `runs/` directory (the workflow `git add`s it) must remain functional and in place during Phase 1.

---

## Classification Table

### Live runtime — KEEP

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| src/seas.py | Keep | Orchestrator; entrypoint of the weekly Action | High |
| src/week.py | Keep | Invoked by seas.py (status gate) | High |
| src/opportunities.py | Keep | Invoked by seas.py; writes opportunities.json | High |
| src/score.py | Keep | Imported by opportunities.py | High |
| src/main.py | Keep | Invoked by seas.py; emits experiment card | High |
| src/experiment.py | Keep | Imported by main.py | High |
| data/signals.json | Keep | Live input; Signal store | High |
| data/opportunities.json | Keep | Live output of opportunities.py | High |
| .github/workflows/seas-weekly.yml | Keep | Runs the system; out of scope this phase | High |
| data/signal_template.json | Keep | Template for the live Signal schema | Low |
| inbox/signals.md | Keep | Signal intake surface (aligned w/ north star) | Medium |

### North-star-aligned reference / research artifacts — KEEP or LEGACY

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| README.md | Keep | Canonical north star (Phase 0) | High |
| AUDIT.md | Keep | This audit; current decision record | Low |
| runs/OPPORTUNITY_SCHEMA.md | Keep | Defines the **Opportunity** noun — the layer the system most needs to formalize | Medium |
| runs/OPPORTUNITY_HEURISTICS.md | Keep | Opportunity evaluation criteria; north-star-aligned | Low |
| prompts/evaluate_opportunity.md | Keep | Opportunity-stage prompt; aligned, reusable | Low |
| prompts/opportunity_generator.md | Legacy | Aligned (Opportunity stage) but not yet wired to code | Low |
| experiments/SEAS-001-agent-organization-lab.md | Keep | Canonical **Experiment** card (real work) | High |
| experiments/SEAS-002-mcp-organization-lab.md | Keep | Canonical **Experiment** card (real work) | High |
| experiments/FALSIFICATION_CRITERIA.md | Keep | Falsification criteria for SEAS-001/002 | Medium |
| findings/F-001-cognitive-operators.md | Keep | Canonical **Finding** (untracked — should be `git add`ed) | High |
| results/agent_organization_lab/emerging_theory.md | Keep | The first **Theory**; promote in a later phase | High |
| results/agent_organization_lab/cognitive_operations.md | Keep | Evidence behind F-001 | Medium |
| results/agent_organization_lab/cross_signal_analysis.md | Keep | Evidence (SEAS-001↔002) behind F-001 | Medium |
| results/agent_organization_lab/comparison.md | Keep | Experiment evidence | Low |
| results/agent_organization_lab/benchmark.md | Keep | Experiment evidence | Low |
| results/agent_organization_lab/early_findings.md | Keep | Experiment evidence | Low |
| results/agent_organization_lab/scoring_rubric.md | Keep | Experiment evidence/method | Low |
| results/agent_organization_lab/raw/critic_output.md | Keep | Raw experiment output | Low |
| results/agent_organization_lab/raw/researcher_output.md | Keep | Raw experiment output | Low |
| results/agent_organization_lab/raw/single_agent.md | Keep | Raw experiment output | Low |
| results/mcp_researcher_output.md | Keep | SEAS-002 raw output | Low |
| results/mcp_single_agent.md | Keep | SEAS-002 raw output | Low |
| runs/SEAS_RESEARCH_QUESTIONS.md | Keep | Active research questions feeding Findings/Theory | Low |

### Experiment-design supporting files — LEGACY (aligned, unwired, kept in place)

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| experiments/agent_organization_lab.md | Legacy | Earlier draft of SEAS-001; aligned concept, redundant with the SEAS-001 card | Low |
| experiments/agent_organization_benchmark.md | Legacy | Benchmark spec for the org lab | Low |
| experiments/researcher_prompt.md | Legacy | Structure prompt used by the org-lab experiment | Low |
| experiments/critic_prompt.md | Legacy | Structure prompt used by the org-lab experiment | Low |
| experiments/judge_prompt.md | Legacy | Structure prompt used by the org-lab experiment | Low |
| experiments/test_prompt.md | Legacy | Org-lab support prompt | Low |
| experiments/mcp_signal.md | Legacy | SEAS-002 signal input | Low |
| experiments/mcp_researcher_prompt.md | Legacy | SEAS-002 structure prompt | Low |
| experiments/mcp_single_agent_prompt.md | Legacy | SEAS-002 structure prompt | Low |

> Note: these live under `experiments/` and are the *inputs* to real Experiments. They are not wired to Python, but they are north-star content, not dead architecture. Leaving them in place avoids breaking the conceptual record. A later phase may consolidate them under each SEAS-00x experiment.

### Superseded capability engine — ARCHIVE

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| src/add_capability.py | Archive | Capability concept removed from north star | Low |
| src/capability_inventory.py | Archive | Capability engine | Low |
| src/capability_analysis_job.py | Archive | Capability engine (Gen 2) | Low |
| src/ingest_capability_analysis.py | Archive | Capability engine (Gen 2) | Low |
| src/link_experiment.py | Archive | Capability-linking | Low |
| src/link_artifact.py | Archive | Capability-linking | Low |
| src/update_capability_status.py | Archive | Capability-linking | Low |
| src/recommend_next_capability.py | Archive | Capability engine | Low |
| data/capabilities.json | Archive | Capability store; not read by live runtime | Low |
| inbox/capabilities.md | Archive | Capability intake | Low |
| runs/CAPABILITY_PIPELINE.md | Archive | Already marked SUPERSEDED (Phase 0) | Low |
| runs/CAPABILITY_SCHEMA.md | Archive | Already marked SUPERSEDED (Phase 0) | Low |
| prompts/capability_analysis.md | Archive | Capability-engine prompt | Low |

### Superseded manual scoring / enrichment / classification — ARCHIVE

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| src/add_signal.py | Archive | Interactive entry; signals now authored in JSON | Low |
| src/score_signals.py | Archive | Interactive scoring; superseded by score.py | Low |
| src/import_scores.py | Archive | Interactive scoring; superseded | Low |
| src/apply_score.py | Archive | Applies llm_scores/latest.json; unwired | Low |
| src/auto_score.py | Archive | Builds scoring job; unwired | Low |
| src/enrich_signal.py | Archive | Enrichment; superseded (fields hand-written) | Low |
| src/apply_enrichment.py | Archive | Enrichment; unwired | Low |
| src/classify_signal.py | Archive | Classification; superseded | Low |
| prompts/score_signal.md | Archive | Prompt for auto_score.py | Low |
| prompts/enrich_signal.md | Archive | Prompt for enrich_signal.py | Low |
| prompts/classify_signal.md | Archive | Prompt for classify_signal.py | Low |
| runs/enrichment/latest.json | Archive | Run artifact of enrichment flow | Low |
| runs/llm_scores/latest.json | Archive | Run artifact of scoring flow | Low |
| runs/scoring/Agent_Evaluation_Harnesses.md | Archive | Manual scoring job log | Low |
| runs/scoring/MCP_Ecosystem_Growth.md | Archive | Manual scoring job log | Low |

### Superseded Gen-2 experiment / frontier flow — ARCHIVE

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| src/frontier_brief.py | Archive | Gen-2 "Frontier Brief" stage; unwired | Low |
| src/generate_experiment_job.py | Archive | Gen-2 experiment generation; superseded by main.py | Low |
| src/opportunity_job.py | Archive | Prototype of opportunities.py (reads runs/test_signal.md) | Low |
| src/build_plan.py | Archive | Gen-2 build-plan step; unwired | Low |
| src/choose_experiment.py | Archive | Gen-2 selection step; unwired | Low |
| prompts/frontier_brief.md | Archive | Gen-2 frontier prompt | Low |
| prompts/frontier_brief_output.md | Archive | Gen-2 frontier prompt | Low |
| prompts/generate_experiment.md | Archive | Gen-2 experiment prompt | Low |
| prompts/experiment_output_format.md | Archive | Gen-2 experiment prompt | Low |
| runs/test_signal.md | Archive | Fixture for opportunity_job.py | Low |
| runs/frontier/latest.json | Archive | Gen-2 run artifact | Low |
| runs/capability_analysis/latest.json | Archive | Gen-2 run artifact | Low |
| runs/experiments/latest.json | Archive | Gen-2 run artifact | Low |
| runs/capability_analysis_job.md | Archive | Gen-2 job log | Low |
| runs/experiment_generation_job.md | Archive | Gen-2 job log | Low |
| runs/opportunity_job.md | Archive | Gen-2 job log | Low |

### Superseded architecture docs — ARCHIVE

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| runs/SEAS_NORTH_STAR.md | Archive | Already marked SUPERSEDED (Phase 0); contradicts README | Low |
| runs/SEAS_V2_ARCHITECTURE.md | Archive | Already marked SUPERSEDED (Phase 0) | Low |
| runs/ARCHITECTURE_REVIEW.md | Archive | Reviews Gen-1 pipeline; historical | Low |

### Stale dated run logs — ARCHIVE

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| runs/2026-06-01-experiment.md | Archive | Dated output log, not architecture | Low |
| runs/2026-06-01-active-experiment.md | Archive | Dated output log | Low |
| runs/2026-06-01-build-plan.md | Archive | Dated output log | Low |
| runs/2026-06-01-frontier-brief-job.md | Archive | Dated output log | Low |

> **Caveat:** the weekly Action runs `git add data/ runs/` and writes `runs/<date>-experiment.md`. Archiving *past* dated logs is safe; the directory itself must remain. See Migration Order step 4.

### Worthless — DELETE LATER

| Path | Class | Reason | Risk of removal |
|---|---|---|---|
| src/ledger.py | Delete Later | 0 bytes; imported by nothing | Low |
| src/scan.py | Delete Later | 0 bytes; imported by nothing | Low |
| data/ledger.json | Delete Later | 0 bytes; read by nothing | Low |
| src/__pycache__/experiment.cpython-39.pyc | Delete Later | Build artifact; should be gitignored | Low |
| src/__pycache__/score.cpython-39.pyc | Delete Later | Build artifact; should be gitignored | Low |

---

## Summary Counts

| Class | Count |
|---|---|
| Keep | 33 |
| Legacy | 10 |
| Archive | 44 |
| Delete Later | 5 |

(Live runtime = 6 Python files + 2 JSON + workflow. ~80% of `src/*.py` is Archive/Delete Later.)

---

## Proposed Archive Folder Structure

A single top-level `archive/`, organized **by superseded generation** so future readers understand *why* something was retired, not just *that* it was:

```
archive/
├── README.md                     ← explains archive policy; nothing here is live
├── capability-engine/
│   ├── src/                      ← add_capability, capability_inventory,
│   │                                capability_analysis_job, ingest_capability_analysis,
│   │                                link_experiment, link_artifact,
│   │                                update_capability_status, recommend_next_capability
│   ├── data/                     ← capabilities.json
│   ├── inbox/                    ← capabilities.md
│   ├── prompts/                  ← capability_analysis.md
│   └── docs/                     ← CAPABILITY_PIPELINE.md, CAPABILITY_SCHEMA.md
├── manual-scoring/
│   ├── src/                      ← add_signal, score_signals, import_scores,
│   │                                apply_score, auto_score, enrich_signal,
│   │                                apply_enrichment, classify_signal
│   ├── prompts/                  ← score_signal.md, enrich_signal.md, classify_signal.md
│   └── runs/                     ← enrichment/latest.json, llm_scores/latest.json,
│                                    scoring/*.md
├── gen2-experiment-flow/
│   ├── src/                      ← frontier_brief, generate_experiment_job,
│   │                                opportunity_job, build_plan, choose_experiment
│   ├── prompts/                  ← frontier_brief.md, frontier_brief_output.md,
│   │                                generate_experiment.md, experiment_output_format.md
│   └── runs/                     ← test_signal.md, frontier/latest.json,
│                                    capability_analysis/latest.json, experiments/latest.json,
│                                    *_job.md
├── architecture-docs/            ← SEAS_NORTH_STAR.md, SEAS_V2_ARCHITECTURE.md,
│                                    ARCHITECTURE_REVIEW.md
└── run-logs/
    └── 2026-06-01-*.md           ← dated output logs
```

Mirroring the original `src/ data/ prompts/ runs/` sub-paths inside each generation folder makes the moves mechanical and the provenance obvious.

---

## Migration Order (for the future execution phase — not now)

Ordered to keep the system green at every step. Use `git mv` so history follows the file.

1. **Baseline green run.** `python src/seas.py` locally; confirm it completes and writes `data/opportunities.json` + `runs/<date>-experiment.md`. Record the output as the rollback reference.
2. **Create `archive/` skeleton** + `archive/README.md`. No moves yet.
3. **Move the lowest-risk, fully-orphaned generations first** (none are imported by live code):
   a. capability-engine (src + data + inbox + prompts + docs)
   b. manual-scoring (src + prompts + runs)
   c. gen2-experiment-flow (src + prompts + runs)
4. **Move superseded docs** (architecture-docs).
5. **Move stale dated run logs** (`runs/2026-06-01-*.md`) into `archive/run-logs/`. **Do not** move/rename the `runs/` directory itself or `runs/<future-date>-experiment.md` targets — the workflow writes there and `git add runs/`.
6. **Re-run `python src/seas.py`.** Must produce identical-structure output to step 1. This is the gate: if it fails, stop and roll back.
7. **Leave `Delete Later` files untouched** in Phase 1 (empty stubs + pyc). Add `__pycache__/` and `*.pyc` to `.gitignore` so artifacts stop reappearing.
8. **`git add findings/`** — F-001 is currently untracked; commit the canonical Finding so the north-star layer is version-controlled.

> Each numbered step (3a, 3b, 3c, 4, 5) is its own commit so any single move is independently revertible.

## What Phase 1 deliberately does NOT touch

- The 6 live Python files, `seas.py`, the workflow.
- `data/signals.json`, `data/opportunities.json`.
- All Keep/Legacy north-star content (experiments/SEAS-00x, results/, findings/, opportunity docs/prompts).
- Any `Delete Later` file (flagged only; removed in a later phase after a green run).

---

## Rollback Strategy

Because Phase 1 is **moves only** (via `git mv`), rollback is cheap and total:

1. **Per-step revert.** Each generation moved in its own commit → `git revert <sha>` restores that generation to its original path with history intact.
2. **Full Phase-1 revert.** If multiple steps are bad: `git revert <first>..<last>` or `git reset --hard <pre-phase-1-sha>` on a throwaway branch.
3. **No data loss possible.** Nothing is deleted in Phase 1; `git mv` preserves blob history, so even a squashed merge keeps content recoverable via `git log --follow`.
4. **Runtime canary = the gate.** Steps 1 and 6 bracket the migration with a `seas.py` run. The migration is only accepted if step 6 matches step 1. A failing canary means revert before committing further.
5. **Recommended safety net.** Do the whole phase on a branch (e.g. `phase-1-quarantine`) and only merge after the canary passes, so `main` always has a working `seas.py`.

---

## Open Decisions to Confirm Before Executing

1. **`experiments/` Legacy prompts** (`researcher_prompt.md`, `critic_prompt.md`, etc.): keep in place (this plan's recommendation) or relocate alongside their SEAS-00x experiment? Recommendation: keep until a later "promote research layer" phase.
2. **`runs/` is overloaded** — it currently mixes architecture docs, schemas, job logs, and dated outputs. Phase 1 archives the obsolete ones; a later phase should split surviving schemas (`OPPORTUNITY_*`) out of `runs/` since they aren't run logs.
3. **Whether to archive `runs/OPPORTUNITY_SCHEMA.md`/`HEURISTICS.md`**: classified **Keep** here because they define the north-star Opportunity layer. Confirm you agree they're source-of-truth, not legacy.
