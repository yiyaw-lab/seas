# SEAS Codebase Audit

Date: 2026-06-02
Auditor: Claude (read-only audit — nothing rewritten)

North star evaluated against:

```
Signal → Opportunity → Experiment → Finding → Theory
```

---

## TL;DR

The repo contains **three overlapping generations of SEAS** living side by side:

1. **Gen 1 — Signal Scoring Engine** (`signals.json` → score → experiment). This is the only thing the automation (`seas.py`, the GitHub Action) actually runs.
2. **Gen 2 — Capability Engine** (`capabilities.json`, frontier brief, capability analysis). Fully built, fully orphaned. Nothing calls it.
3. **Gen 3 — Opportunity → Finding → Theory research loop** (the `experiments/SEAS-00x`, `results/agent_organization_lab/`, `findings/F-001`). This is where your *actual recent work and conceptual energy* is — but it is **100% manual / document-driven and has zero Python backing it.**

**The painful gap:** the code that runs (Gen 1) is the *least* aligned with the north star, and the work aligned with the north star (Gen 3) has *no code* — it's prose files produced by hand. The middle stage everyone keeps rewriting ("Capability") **has been deleted from the north star** but still owns the most source files.

The single most valuable move is **not** cleanup. It is **promoting the Finding/Theory layer to a real data model and making `Opportunity` the bridge that the runnable pipeline actually emits.**

---

## 1. Current Architecture Diagram

### 1a. North star (target)

```
Signal ──▶ Opportunity ──▶ Experiment ──▶ Finding ──▶ Theory
```

### 1b. What actually executes today (`seas.py` / weekly Action)

```
inbox/signals.md
      │  (process_inbox.py — run manually, not by seas.py)
      ▼
data/signals.json ──────────────┐
      │                         │
      │ seas.py                 │
      ▼                         │
  week.py (status print)        │
      │                         │
      ▼                         │
opportunities.py ── score.py ──▶ data/opportunities.json
      │
      ▼
  main.py ── experiment.py ──▶ runs/YYYY-MM-DD-experiment.md
```

Note: this stops at "experiment.md". **It never reaches Finding or Theory.** And "Opportunity" here is just a re-sorted Signal (same fields + weighted_score) — not a distinct concept.

### 1c. What the north-star work actually looks like (manual, no code)

```
experiments/SEAS-001-agent-organization-lab.md   (Experiment, hand-authored)
      ▼
results/agent_organization_lab/*.md              (raw outputs, comparison, scoring)
      ▼
results/agent_organization_lab/emerging_theory.md
      ▼
findings/F-001-cognitive-operators.md            (Finding → Theory, hand-authored)
```

This is the **real SEAS**. None of the `src/` code produced any of it.

### 1d. Orphaned Gen-2 capability machinery (built, never wired)

```
frontier_brief.py ─▶ runs/frontier/latest.json
capability_analysis_job.py ─▶ runs/capability_analysis/latest.json
ingest_capability_analysis.py ─▶ data/capabilities.json
add_capability / link_experiment / link_artifact / update_capability_status / recommend_next_capability / capability_inventory
```

Nothing in `seas.py` or the workflow touches any of these.

---

## 2. Dead Code Report

### 2a. Empty stubs (delete outright)

| File | Status |
|------|--------|
| [src/ledger.py](src/ledger.py) | 0 bytes |
| [src/scan.py](src/scan.py) | 0 bytes |
| [data/ledger.json](data/ledger.json) | 0 bytes — referenced by nothing |
| `src/__pycache__/` | build artifact, should be gitignored |

### 2b. Orphaned but functional (never imported, never invoked by `seas.py` or CI)

These all *work*, but nothing calls them. They are vestiges of Gen 1 (manual scoring) and Gen 2 (capability engine):

**Gen 1 manual-scoring leftovers (superseded by auto-scored `signals.json`):**
- [src/add_signal.py](src/add_signal.py) — interactive `input()` signal entry
- [src/score_signals.py](src/score_signals.py) — interactive scoring
- [src/import_scores.py](src/import_scores.py) — interactive scoring
- [src/apply_score.py](src/apply_score.py) — applies `runs/llm_scores/latest.json`
- [src/auto_score.py](src/auto_score.py) — builds a scoring job from a prompt
- [src/enrich_signal.py](src/enrich_signal.py) + [src/apply_enrichment.py](src/apply_enrichment.py)
- [src/classify_signal.py](src/classify_signal.py)

**Gen 2 capability engine (concept removed from north star entirely):**
- [src/frontier_brief.py](src/frontier_brief.py)
- [src/capability_analysis_job.py](src/capability_analysis_job.py)
- [src/ingest_capability_analysis.py](src/ingest_capability_analysis.py)
- [src/add_capability.py](src/add_capability.py)
- [src/capability_inventory.py](src/capability_inventory.py)
- [src/link_experiment.py](src/link_experiment.py)
- [src/link_artifact.py](src/link_artifact.py)
- [src/update_capability_status.py](src/update_capability_status.py)
- [src/recommend_next_capability.py](src/recommend_next_capability.py)

**Gen 2 experiment-flow leftovers (superseded by `main.py`/`experiment.py`):**
- [src/generate_experiment_job.py](src/generate_experiment_job.py)
- [src/opportunity_job.py](src/opportunity_job.py) — reads `runs/test_signal.md`
- [src/build_plan.py](src/build_plan.py)
- [src/choose_experiment.py](src/choose_experiment.py)

**Count:** of 31 `src/*.py` files, only **6 are live** (`seas.py`, `week.py`, `opportunities.py`, `score.py`, `main.py`, `experiment.py`). ~80% of the Python is dead or orphaned.

---

## 3. Obsolete Scripts List (ranked by confidence)

**Definitely obsolete — concept deleted from north star (Capability layer):**
all 9 `*capability*` / capability-linking scripts in §2b, plus `data/capabilities.json`, `inbox/capabilities.md`, `runs/CAPABILITY_*.md`.

**Obsolete — replaced by a newer mechanism:**
- Interactive entry/scoring scripts (`add_signal`, `score_signals`, `import_scores`, `apply_score`) — signals are now authored directly in `signals.json` with scores already filled.
- `enrich_signal` / `apply_enrichment` / `classify_signal` — enrichment fields (`summary`, `category`, `capability`) are now written by hand into `signals.json`.
- `opportunity_job.py` / `runs/test_signal.md` — single-signal prototype of the opportunity step, replaced by `opportunities.py`.

**Obsolete docs (describe abandoned architectures):**
- [runs/SEAS_V2_ARCHITECTURE.md](runs/SEAS_V2_ARCHITECTURE.md) — Gen 2 "Frontier Brief → Capability Analysis" pipeline.
- [runs/SEAS_NORTH_STAR.md](runs/SEAS_NORTH_STAR.md) — says "SEAS is a **capability acquisition engine**." **Directly contradicts the current north star.**
- [runs/CAPABILITY_PIPELINE.md](runs/CAPABILITY_PIPELINE.md), [runs/CAPABILITY_SCHEMA.md](runs/CAPABILITY_SCHEMA.md)
- [runs/ARCHITECTURE_REVIEW.md](runs/ARCHITECTURE_REVIEW.md) — Gen 1 pipeline review.

**Stale run artifacts (outputs, not source):** everything matching `runs/2026-06-01-*.md` and `runs/*/latest.json`. These are *logs*, not architecture. Keep for history or move to an `archive/` — they shouldn't sit next to schema docs.

---

## 4. Duplicate Functionality Report

| Concern | Duplicated across | Recommendation |
|---|---|---|
| **Scoring logic** | `score.py` (live), `score_signals.py`, `import_scores.py`, `apply_score.py`, `auto_score.py` | Keep `score.py` as the only scorer. Delete the other 4. |
| **Signal → Opportunity ranking** | `opportunities.py` (live), `score.py:main()`, `opportunity_job.py` | `score.py:main()` and `opportunity_job.py` are redundant prototypes of `opportunities.py`. |
| **Experiment generation** | `experiment.py` + `main.py` (live), `generate_experiment_job.py`, `build_plan.py`, `choose_experiment.py` | The latter three are an abandoned multi-step variant. Collapse to one path. |
| **"Opportunity" definition** | `data/opportunities.json` (a sorted signal) vs. `runs/OPPORTUNITY_SCHEMA.md` / `OPPORTUNITY_HEURISTICS.md` (a richer concept) | **The code and the schema doc disagree about what an Opportunity is.** This is the most important duplicate to resolve — see §6. |
| **Signal store** | `data/signals.json` (live) vs. `inbox/signals.md` (entry) vs. `data/signal_template.json` | Fine as-is, but `process_inbox.py` is not run by `seas.py`, so inbox→store is a manual hop. |
| **North star statements** | `runs/SEAS_NORTH_STAR.md` ("capability engine") vs. your stated north star (Signal→…→Theory) | Conflicting. New source of truth needed. |

---

## 5. What Should Become the Source of Truth

| Layer | Today's de-facto source | Problem | Should become |
|---|---|---|---|
| **North star** | `runs/SEAS_NORTH_STAR.md` | Says the wrong thing | A new top-level `README.md` (currently empty) stating Signal→Opportunity→Experiment→Finding→Theory |
| **Signal** | `data/signals.json` | OK | Keep |
| **Opportunity** | `data/opportunities.json` | It's just a re-sorted signal; no distinct identity | Promote to first-class records with IDs |
| **Experiment** | `experiments/SEAS-00x-*.md` (manual) + `runs/*-experiment.md` (auto) | Two unrelated notions of "experiment" coexist | The `SEAS-00x` markdown cards are the real ones. The auto `runs/*-experiment.md` are toy stubs. |
| **Finding** | `findings/F-001-*.md` | Good, but unmodeled | Keep as canonical; give it light frontmatter |
| **Theory** | `results/agent_organization_lab/emerging_theory.md` + inside F-001 | Theory is buried in two places | Promote to its own `theories/` space |

---

## 6. Recommended Data Model

The core insight: **make the five north-star nouns real, linked records.** IDs are the spine.

```
Signal      S-001   { id, title, source, category, summary, scores{...}, weighted_score }
                       └─ scoring stays exactly as score.py does it
Opportunity O-001   { id, signal_id, statement, capability_thesis, thinking_mode?, qualifies }
                       └─ NOT just a sorted signal — a *framed bet* derived from a signal
Experiment  E-001   { id, opportunity_id, hypothesis, structures[], task, eval_criteria[],
                       deliverables[], status }            ← this is your SEAS-00x cards
Finding     F-001   { id, experiment_ids[], statement, evidence[], confidence, status }
                       └─ already exists as findings/F-001; just formalize
Theory      T-001   { id, finding_ids[], claim, predictions[], open_questions[], status }
                       └─ "Agent structures = cognitive operators" is your first Theory
```

Each record links *up* (to its parent) and is the single source for its layer. The chain becomes traceable: `T-001 ← F-001 ← E-001/SEAS-001 ← O-001 ← S-001 (Claude Code Subagents)`.

Recommendation: keep the leaf layers (Experiment/Finding/Theory) as **markdown with YAML frontmatter** (you think in prose there, and that's correct — "conceptual clarity over code cleanliness"). Keep Signal/Opportunity as **JSON** (they're tabular and scored). Don't force the research layer into JSON.

---

## 7. Recommended Folder Structure

Reorganize *by north-star noun*, so the folder tree literally is the architecture:

```
seas/
├── README.md                  ← real north star (replace empty file)
├── seas.py                    ← orchestrator (move out of src/ or keep, but it's the entrypoint)
│
├── signals/
│   ├── signals.json
│   └── inbox.md               ← was inbox/signals.md
├── opportunities/
│   └── opportunities.json
├── experiments/
│   ├── SEAS-001-agent-organization-lab.md
│   └── SEAS-002-mcp-organization-lab.md
├── findings/
│   └── F-001-cognitive-operators.md
├── theories/
│   └── T-001-cognitive-operators.md   ← promote from results/.../emerging_theory.md
│
├── results/                   ← raw experiment outputs only (evidence for findings)
│   └── agent_organization_lab/
│
├── lib/                       ← the 6 live scripts only
│   ├── score.py
│   ├── opportunities.py
│   ├── experiment.py
│   ├── main.py
│   └── week.py
├── prompts/                   ← keep ones tied to live/near-term flow; archive the rest
│
└── archive/                   ← everything obsolete, kept for history, out of the way
    ├── capability-engine/     ← the 9 capability scripts + capabilities.json + CAPABILITY_*.md
    ├── manual-scoring/        ← add_signal, score_signals, import_scores, apply_score, etc.
    ├── gen2-architecture/     ← SEAS_V2_ARCHITECTURE.md, frontier_brief.py, etc.
    └── runs/                  ← dated run logs + */latest.json
```

The win: a new reader opening the repo sees `signals / opportunities / experiments / findings / theories` and *immediately understands SEAS*. The folder tree teaches the architecture.

---

## 8. Migration Plan (phased, no rewrites until you approve)

**Phase 0 — Stop the contradiction (5 min, zero risk)**
- Write the real north star into the empty `README.md`.
- Mark `runs/SEAS_NORTH_STAR.md` and `runs/SEAS_V2_ARCHITECTURE.md` as superseded (or move to `archive/`).

**Phase 1 — Quarantine the dead (low risk)**
- Delete `src/ledger.py`, `src/scan.py`, `data/ledger.json`, `src/__pycache__/`; add `__pycache__/` to `.gitignore`.
- Move the 9 capability scripts + `data/capabilities.json` + `inbox/capabilities.md` + `runs/CAPABILITY_*.md` to `archive/capability-engine/`.
- Move manual-scoring + enrichment + classify scripts to `archive/manual-scoring/`.
- Move `generate_experiment_job.py`, `opportunity_job.py`, `build_plan.py`, `choose_experiment.py`, `frontier_brief.py` to `archive/gen2-architecture/`.
- Run `seas.py` once to confirm the live pipeline still works (it imports nothing from the moved files).

**Phase 2 — Restructure by noun (medium risk — touches paths)**
- Create `signals/ opportunities/ experiments/ findings/ theories/ lib/`.
- Move the 6 live scripts to `lib/`; fix the 3 hardcoded relative paths (`data/...`) inside them.
- Move dated run logs into `archive/runs/`.

**Phase 3 — Promote the research layer (the actual value)**
- Extract `results/agent_organization_lab/emerging_theory.md` → `theories/T-001-cognitive-operators.md` with frontmatter linking `finding_ids: [F-001]`.
- Add frontmatter IDs to `F-001` and the `SEAS-001/002` cards so the chain is machine-traceable.

**Phase 4 — Close the architecture gap (the real project)**
- Make `Opportunity` a first-class record (not a sorted signal): `opportunities.py` should emit framed bets with IDs, not re-sorted signals.
- Decide whether `experiment.py`/`main.py` (toy auto-experiments) should be *replaced* by the SEAS-00x card workflow — currently the runnable pipeline produces throwaway experiments while the real experiments are hand-written. **This is the central design decision SEAS hasn't made yet.**

---

## Three things I'd flag to a human before any cleanup

1. **The runnable pipeline and the real work are disconnected.** `seas.py` produces `runs/*-experiment.md` that nobody uses; the experiments that matter (`SEAS-001/002`) were written by hand. Cleaning code won't fix that — you have to decide if SEAS *generates* experiments or *helps you author* them.

2. **"Opportunity" has no real definition.** In code it's a sorted signal; in `OPPORTUNITY_SCHEMA.md` it's something richer. Until you pin this down, the second arrow of your north star is hollow.

3. **The most aligned artifact in the repo — F-001 / the cognitive-operators theory — is the least supported by infrastructure.** If conceptual clarity is the goal, that's the layer to build *up* to, not the code to clean *down*.
