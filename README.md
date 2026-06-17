<div align="center">

# SEAS + Argo

**A frontier research engine and a self-improving agentic scout — judgment as the spine.**

*Generation is free. Judgment isn't.*

[![Tests](https://github.com/yiyaw-lab/seas/actions/workflows/tests.yml/badge.svg)](https://github.com/yiyaw-lab/seas/actions/workflows/tests.yml)
&nbsp;[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](https://www.python.org/)
&nbsp;[![License](https://img.shields.io/badge/license-source--available-lightgrey.svg)](LICENSE)
&nbsp;[![Core](https://img.shields.io/badge/core-pure%20stdlib-success.svg)](requirements.txt)
&nbsp;[![Argo](https://img.shields.io/badge/Argo-live%20on%20Railway%20%2B%20Telegram-7d4fff.svg)](#argo--frontier-scout-live-on-railway--telegram)

**41 modules · ~12,000 lines · 315 tests, zero network · 10 pinned deps · a pure-stdlib core**

</div>

> Source-available (see [LICENSE](LICENSE)). Stdlib-first Python 3.11 — the core
> (gate, world model, probes) is pure standard library; thin pinned deps live only
> at the I/O edge (`requirements.txt`).

Two complementary systems for working at the frontier of AI — and a third thing
they add up to: a machine that improves its own judgment under a human gate.

- **SEAS** — a **research engine**. Turns frontier signals into evidence-grounded
  knowledge. Asks: *"What is true?"*
- **Argo** — a **decision + insight engine** and live agentic scout. Turns
  knowledge into action, and drafts upgrades to itself. Asks: *"What should I do
  next?"*, *"What is everyone missing?"*, and *"How should I get better?"*

SEAS generates understanding. Argo generates motion — and, increasingly, its own
next capability.

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  SEAS — research engine      │        │  Argo — frontier scout       │
│  "What is true?"             │ ─────► │  "What should I do next?"    │
│  → findings & theories       │        │  → one project worth building │
└──────────────────────────────┘        └──────────────────────────────┘
```

---

## SEAS — Frontier Research Engine (V3: the reasoning spine)

Turns frontier signals into **genuine, evidence-grounded findings** — and an
updating world model of beliefs. The point of V3: a finding must be earned, not
asserted.

```
Signal → score → topical sources → synthesis → EMISSION GATE → finding | probe
                                                                   ↓
                                            belief (world model) + prediction
```

| Concept | Definition |
|---|---|
| **Signal** | Something that changed on the frontier (from feeds, with its source link). |
| **Finding** | A claim grounded in **cross-source convergence**, with cited real quotes, a falsifiable dated prediction, and a refutation condition. Rejected at the gate otherwise. |
| **Probe** | An honest dead end (`inconclusive` / `premature` / `unreachable`) — so SEAS remembers what it looked at and didn't re-investigate it. |
| **Belief** | A finding promoted into the world model; confidence moves only via evidence or a scored prediction, never by assertion. |
| **Prediction** | A dated, checkable forecast a finding implies — reality scores it later, moving the belief's confidence. |

The gate is the heart: it stops SEAS laundering a signal's own description back
out as a "finding," and it verifies cited quotes are **real substrings** of the
fetched sources (no fabricated evidence).

| Module | Role |
|---|---|
| [`seas_schema.py`](src/seas_schema.py) | Finding schema + the emission gate (the contract). |
| [`seas_finding.py`](src/seas_finding.py) | Stage 1 synthesis: fetch sources, model proposes, gate disposes. |
| [`world_model.py`](src/world_model.py) | Beliefs + evidence-only confidence revision. |
| [`probes.py`](src/probes.py) | Dead-end memory + per-source failure ledger. |
| [`seas_benchmark.py`](src/seas_benchmark.py) | Objective model A/B via the gate (quality, cost, throttle). |

Run the full pipeline: `PYTHONPATH=src python3 src/seas.py` (fetch → score → rank
→ synthesize), or the `SEAS Findings` workflow. Findings persist as JSON in
`findings/` with a `runs/<id>/` source bundle; beliefs in `data/world_model.json`;
dead ends in `data/probes.json`. Needs `FIRECRAWL_API_KEY` for topical
related-source search (else it honestly probes `premature` — see
[docs/FIRECRAWL_SETUP.md](docs/FIRECRAWL_SETUP.md)).

**Demonstrable, committed evidence** (not a claim — these are real pipeline runs):
[`findings/`](findings/README.md) holds gate-passed findings F-002…F-004, each
grounded in two independent sources with quotes verified as real substrings;
[`data/probes.json`](data/probes.json) holds honest dead-ends, including one where
the gate **caught a fabricated quote**; [`data/benchmark_results.json`](data/benchmark_results.json)
is an Opus-vs-Sonnet A/B scored by the gate. The full walkthrough — how a finding
is earned, and the answer to "is this just a GPT wrapper?" — is in
[docs/SEAS_PIPELINE.md](docs/SEAS_PIPELINE.md).

> The legacy `Signal → Opportunity → Experiment` scripts are archived under
> [`archive/src-legacy/`](archive/) (superseded by the V3 gate pipeline). Prose
> F-001 is kept in `findings/` as the deliberate pre-gate negative example.
> Inventory in [AUDIT.md](docs/audits/AUDIT.md); session history in [docs/build-log/](docs/build-log/).

---

## Argo — Frontier Scout (live on Railway + Telegram)

Argo is a two-way, always-on, agentic Telegram companion. It watches the
frontier, surfaces what matters, and can extend its own capabilities — all with a
deliberate human gate.

**The product is one project, not a report.**

### What Argo does

- **Two-way chat** — text the bot, it replies in a frontier-scout voice (Claude,
  model-routed: Sonnet for routine turns, Opus for high-stakes ones) with
  persistent memory (append-only log on a Railway volume). A bare `1-10` records
  a project's energy score.
- **Reads the live web** — `web_fetch` + `verify_feed` + `list_feeds` via an MCP
  server, allowlisted to approved frontier sources (arXiv, GitHub, Hugging Face,
  OpenAI, Anthropic, xAI, Google AI). Feeds are managed via `data/feeds.json`
  (data, not code).
- **Reads any GitHub repo** — `github_read_file` + `github_list` to reason about
  actual projects, not just trending titles.
- **Learns from your feed** — text Argo a **screenshot** and it sees it (Claude
  vision), extracts a durable *taste signal* (the pattern you liked + why), and
  folds it into future projects (`data/taste_signals.json`). Soft preference, kept
  out of the finding gate.
- **Studies any source you point it at** — `study_url(url)` reads a specific page
  you send, even off the allowlist (you're the trust gate). SSRF-guarded; content
  is treated as untrusted data. Argo's *own* browsing stays allowlist-locked.
- **Reads SEAS findings** — `read_findings` couples the research engine to the
  scout: Argo can ground a take in what SEAS has actually concluded.
- **Proactive frontier alerts (tripwire)** — fetches feeds, LLM-judges new items
  ("would a frontier builder care?"), texts up to 3 real alerts + links per run.
  Runs daily at 14:00, 19:00, and 00:00 UTC via the schedule runner.
- **Weekly project** — generates a fresh project bet every Friday 15:00 UTC and
  texts it. Rate it 1-10 to teach Argo your taste; reply SELECT to lock it in.
- **Adversarial rehearsal (SELECT / REHEARSE)** — SELECT stress-tests the project
  through 3 critics and a judge (SHIP / REVISE / KILL) before scaffolding. REHEARSE
  runs the same gauntlet on any project without locking it in. `argo_rehearse.py`.
- **Self-model** — durable self-belief store (`data/argo_self.json`, Railway volume)
  seeded with Argo's identity and updated via `note_self_lesson`. Weekly reflection
  call distils performance data (energy ratings, tripwire counts) into honest lessons.
  `argo_self.py`.
- **Self-status** — `get_webhook_health`, `get_latest_project`,
  `get_signal_freshness`: Argo reports its own health honestly.
- **Self-heal** — `reregister_webhook` + `refetch_signals`, gated by
  `ARGO_HEAL_LEVEL`. L0 = report-only (default); L1 = proposes a fix, executes
  only on your Telegram "CONFIRM" reply.
- **Self-create** — `propose_change` opens a GitHub PR so Argo can draft a new
  capability (feed, tool, schedule) for your review. It never self-merges.
  New feeds and schedules are data Argo can propose; workflows require a human.

### How Argo improves itself

Argo is not generated once and left alone — it runs three self-improvement loops,
each ending at a **human merge gate** (Argo can open a PR, never merge one).
Confidence is *earned*: evidence moves a belief ±0.05, a scored prediction ±0.20,
assertion never.

- **Self-diagnosis** (`argo_incidents` + `argo_diagnose`) — Argo logs its own
  operational failures, clusters them by signature, and when one recurs it
  diagnoses the likely cause, stages a fix behind a Telegram **FIX** gate, opens a
  PR, polls CI, and confirms only after a quiet post-deploy window — then moves the
  self-belief the fix was meant to settle.
- **Frontier-evolution** (`argo_evolve` + `argo_predictions`) — on a schedule Argo
  watches release feeds for its own stack (models, SDKs, MCP), maps anything new
  against an honest self-description, and at most once a day texts **one** upgrade
  lever: *"X shipped — I could adopt it in Y. EVOLVE or SKIP."* EVOLVE stress-tests
  a major lever through Argo's own adversaries (a KILL is final), drafts a real PR,
  and records a **dated prediction** that reality scores later — so an adopted
  upgrade has to prove itself, not just sound good.
- **Capability-gap proposer** (the inward twin) — same lever ledger, same
  EVOLVE/SKIP gate, but the signal is Argo's *own* gaps: the honest "not used" list
  in its stack manifest plus its unresolved self-beliefs. It proposes the upgrades
  that close what Argo is missing, not only what the frontier just shipped.

```
spot (failure | frontier | own gap) → rehearse / diagnose → propose_change → PR
   → you review → you merge → Railway redeploys → prediction scored by reality
```

Generator (Argo) / reviewer / merger (you) stay three separate roles, with the
merge as the safety gate. Proven in practice: Argo drafted **PR #1** (add an arXiv
cs.SE feed); we reviewed and merged it. The same spine now drafts upgrades to
Argo's own stack.

### Design docs

- [docs/SEAS_PIPELINE.md](docs/SEAS_PIPELINE.md) — how a finding is earned (the gate, a worked example, the benchmark)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Procfile, workflows, health endpoint, env tiers
- [docs/MULTI_USER_ROADMAP.md](docs/MULTI_USER_ROADMAP.md) — honest single-user status + the path to multi-tenant
- [docs/FIRECRAWL_SETUP.md](docs/FIRECRAWL_SETUP.md) — optional source-search key + why the stdlib client
- [docs/architecture/ARGO_ARCHITECTURE.md](docs/architecture/ARGO_ARCHITECTURE.md) — V1 decision engine (frozen)
- [docs/architecture/ARGO_V2.md](docs/architecture/ARGO_V2.md) — V2 insight engine (approved design)
- [docs/plans/ARGO_V2_MIGRATION.md](docs/plans/ARGO_V2_MIGRATION.md) — V1 → V2 path
- [docs/audits/AUDIT.md](docs/audits/AUDIT.md) — codebase audit (pre-archive generational history)
- [docs/build-log/](docs/build-log/) — dated session logs (newest first)
- [docs/ARGO_WEBHOOK_SETUP.md](docs/ARGO_WEBHOOK_SETUP.md) — Railway + webhook setup
- [docs/ARGO_LLM_SETUP.md](docs/ARGO_LLM_SETUP.md) — API key + model config

---

## Repository Layout

```
README.md                    ← you are here
CLAUDE.md                    ← behavioral rules + project conventions for Claude Code

docs/
  ARGO_WEBHOOK_SETUP.md, ARGO_LLM_SETUP.md, TELEGRAM_SETUP.md  ← setup guides
  architecture/              ← ARGO_ARCHITECTURE.md (V1), ARGO_V2.md (V2 design)
  plans/                     ← ARGO_V2_MIGRATION.md, PHASE_1_PLAN.md
  audits/                    ← AUDIT.md (codebase audit)

src/
  argo_webhook.py            ← two-way Telegram chat (ASGI: Flask + FastMCP)
  argo_mcp_server.py         ← MCP server: web_fetch, verify_feed, list_feeds,
                                github_read_file, github_list, self-status + heal
                                tools, propose_change (E4)
  argo_observe.py            ← LLM call layer (providers + chat_with_mcp + guardrails)
  argo_guard.py              ← resilience: retry/backoff, circuit breaker, daily budget
  argo_project.py            ← generate a fresh LLM project + send to Telegram
  argo_rehearse.py           ← adversarial rehearsal: 3 critics + a judge, SHIP/REVISE/KILL
  argo_rating.py             ← rating + project-state helpers (parse/target/record/select)
  argo_github.py             ← GitHub read API (allowlist + gh_api; backs the github_* tools)
  argo_watch.py              ← tripwire: proactive frontier alerts
  argo_memory.py             ← shared append-only chat log (webhook + proactive senders)
  argo_self.py               ← self-model: live capability inventory, self-belief store,
                                weekly reflection; seed_identity() on first startup
  argo_scheduled.py          ← hourly schedule runner (reads data/schedule.json)
  argo_rate.py               ← read Telegram replies for energy ratings
  taste_signals.py           ← durable taste-signal store (load/save/add; backs
                                taste learning from screenshots and URLs)
  fetch_signals.py           ← RSS ingestion (reads data/feeds.json)
  profile.py                 ← active user's identity/persona/voice (data/profile.json;
                                copy from data/profile.example.json to customise)
  argo.py                    ← V1 weekly bet + energy (interactive)
  send_telegram.py, set_webhook.py, seas_demo.py  ← delivery + utilities

  self-improvement loops (Argo drafts upgrades to itself; you merge):
  argo_diagnose.py           ← self-diagnosis: cluster failures → stage fix → PR → verify → confirm
  argo_incidents.py          ← operational-failure ledger (what Argo reads back about itself)
  argo_evolve.py             ← frontier-evolution + capability-gap proposer: watch/scan → EVOLVE → PR → score
  argo_predictions.py        ← dated, machine-scored predictions (reality grades the judgment)

  SEAS V3 pipeline:
  seas.py                    ← orchestrator: fetch → score → rank → synthesize
  seas_finding.py            ← Stage 1 synthesis (model proposes, gate disposes)
  seas_schema.py             ← finding schema + the emission gate
  world_model.py             ← beliefs + evidence-only confidence revision
  probes.py                  ← dead-end memory + per-source failure ledger
  seas_benchmark.py          ← objective model A/B via the gate
  opportunities.py, score.py, fetch_signals.py  ← rank + score + ingest
  firecrawl_client.py        ← optional topical source search (stdlib, allowlisted)
  (legacy Gen-1/2 scripts archived under archive/src-legacy/ — see docs/audits/AUDIT.md)

  shared-utils layer (the Argo core builds on these):
  argo_paths.py              ← single source of truth for ROOT + named data paths
  argo_store.py              ← load_json/save_json (indent=2 + trailing newline)
  argo_http.py               ← tls_context() (certifi-backed TLS for all urllib calls)
  argo_log.py                ← get_logger() (operator-facing logging)
  (the older SEAS job scripts still use cwd-relative data/ paths — future work)

data/
  feeds.json                 ← approved signal sources (data; Argo can propose edits)
  schedule.json              ← delivery schedules (data; Argo can propose edits)
  world_model.json           ← SEAS beliefs (committed durable knowledge)
  probes.json                ← SEAS dead-end memory (committed)
  benchmark_results.json     ← model A/B over the gate (committed)
  profile.example.json       ← profile schema template (copy to profile.json)
  argo_self.example.json     ← self-belief store schema template
  — runtime state below is gitignored (per-deploy / Railway volume) —
  signals.json, opportunities.json   ← transient signal cache + ranking
  argo_projects.json, argo_bets.json, argo_seen.json  ← Argo project/bet/seen state
  argo_chat.json, argo_self.json, taste_signals.json  ← chat memory, self-model, taste
  profile.json               ← active user identity/persona (never committed)

archive/src-legacy/  superseded Gen-1/2 SEAS modules (not part of the live runtime)
docs/         SEAS_PIPELINE.md, FIRECRAWL_SETUP.md, DEPLOYMENT.md, MULTI_USER_ROADMAP.md,
              the *_SETUP guides, architecture/, plans/, audits/, build-log/
demo/         V3 pipeline walkthrough + sample Argo project message
findings/     finding log (F-002…F-004 gate-passed; F-001 kept as negative example)
experiments/  SEAS-00x experiment cards
```

## Automation

| Workflow | Effective schedule | What it does |
|---|---|---|
| `argo-schedule.yml` | Hourly (UTC) — the dispatcher | Runs `argo_scheduled.py`; fires whatever is due per `data/schedule.json` with a 3-hour grace window. Drives the project and tripwire deliveries below. |
| `seas-friday-telegram.yml` | **Fridays 15:00 UTC** (via hourly runner) | Generates a fresh weekly project bet and sends it to Telegram. |
| `argo-watch.yml` | **Daily 14:00 / 19:00 / 00:00 UTC** (via hourly runner) | Tripwire sweep — fetches feeds, judges new items, sends up to 3 frontier alerts. |
| `tests.yml` | Every push / PR | Runs the unit suite (read-only, never commits). |
| `seas-findings.yml` | Manual (`workflow_dispatch`) | Runs the SEAS V3 pipeline; commits findings + beliefs + probes + source bundles. |

> Schedules are data, not code — add or change a delivery by editing `data/schedule.json`.
> No workflow file needs to change. Argo can propose schedule edits via its Contents-only PR token.
>
> The volume-bound commands (self-diagnosis, weekly reflection, frontier-evolution,
> and the capability-gap proposer) run in the webhook's **in-process scheduler**
> against the Railway volume — not on Actions, whose fresh checkout lacks the
> ledgers and the staging file their human gates read.

## Testing

```
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run under `python3` (3.11), not the 3.9 `.venv`. The suite is stdlib `unittest`
(no extra dep) and covers the four regressions that kept recurring:

- **scheduler** firing / grace-window / per-day dedupe (`argo_scheduled`)
- **seen-store** dedup + legacy-list migration (`argo_watch`)
- **rating prompt** + decimal ratings (`argo_project.project_invite`,
  `argo_rating.parse_rating` via `argo_webhook._parse_rating`)
- **project re-anchoring** / last-shown targeting (`argo_rating` via
  `argo_webhook._target_project`, `_match_existing_project`)

Tests are pure — no network, no LLM, no real `data/*.json`. They override the
module-level path constants (`SEEN_PATH`, `PROJECTS_LOG`, `SCHEDULE_PATH` /
`STATE_PATH`, `CHAT_LOG_PATH`, `TASTE_PATH`) to a temp dir. Rule: a bug fix in
any of those areas must add or extend a test that fails before the fix and
passes after. New coverage (315 tests total):

- **self-improvement loops** — self-diagnosis gates + proposal lifecycle
  (`test_diagnose.py`, `test_incidents.py`), the frontier-evolution funnel +
  EVOLVE/SKIP gate + the capability-gap proposer (`test_evolve.py`), and dated
  prediction scoring (`test_predictions.py`)

- **chat memory** — roundtrip, per-chat filtering, int/str chat_id unification,
  corrupt-file recovery (`test_memory.py`)
- **taste IDs** — max+1 not len+1, survives deletion, malformed IDs ignored
  (`test_taste_ids.py`)
- **image routing** — screenshot routes through conversational path (history +
  tools), no forced taste write, graceful degradation without vision key
  (`test_image_routing.py`)
- **self-model** — belief store roundtrip, confidence clamping, evidence/refutation
  moves, reflection stats, graceful-on-missing-files (`test_self.py`)
- **temperature guard** — Anthropic Opus and OpenAI reasoning models omit
  `temperature`; standard models keep it (`test_temperature_guard.py`)
- **SEAS pipeline** — signal ranking/qualification, inbox merge, dedup against
  existing, zeroed-score defaults (`test_seas_pipeline.py`)
- **watch model** — tripwire judge skips model call when nothing new
  (`test_watch_model.py`)
- **project selection** — project re-anchoring and last-shown targeting
  (`test_project_selection.py`)
- **health endpoint** — `/` returns valid JSON from local files only, on a fresh
  deploy (stores absent) and a running one, and never raises (`test_health.py`)

## Quickstart (what works at each key tier)

```
PYTHONPATH=src python3 -m unittest discover -s tests   # 315 tests, no keys needed
```

| You have… | What runs |
|---|---|
| **no keys** | the full test suite + all pure logic (the gate, ranking, world model) |
| **+ an LLM key** (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) | `python3 src/seas.py` scores and synthesizes, but usually emits `premature` probes (no related-source search) |
| **+ `FIRECRAWL_API_KEY`** | the full finding path — cross-source convergence, real findings (see [docs/FIRECRAWL_SETUP.md](docs/FIRECRAWL_SETUP.md)) |

Argo (the live Telegram bot) additionally needs `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID`; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Required env (Railway Variables)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (primary chat model) |
| `OPENAI_API_KEY` | gpt-4o fallback |
| `TELEGRAM_BOT_TOKEN` | bot identity |
| `TELEGRAM_CHAT_ID` | your chat |
| `WEBHOOK_URL` | public Railway URL (self-registers webhook on boot) |
| `ARGO_MCP_TOKEN` | bearer auth for the `/mcp` endpoint |
| `ARGO_CHAT_LOG` | `/data/argo_chat.json` (Railway volume mount) |
| `GITHUB_TOKEN` | fine-grained token: Contents read+write + PRs write (repo read) |
| `ARGO_PROPOSE_TOKEN` | same or separate token for `propose_change` (PR creation) |
| `ARGO_HEAL_LEVEL` | `L1` for confirm-in-chat self-heal (default `L0` = report-only) |
| `ARGO_SELF_PATH` | `/data/argo_self.json` (Railway volume; self-belief store) |
| `ARGO_TASTE_PATH` | `/data/taste_signals.json` (Railway volume; taste signals) |
| `ARGO_*_PATH` (self-improvement) | point the incident/proposal ledgers, evolution + prediction stores, and the pending-heal/evolve slots at the `/data` volume so they survive redeploys (`ARGO_INCIDENTS_PATH`, `ARGO_PROPOSALS_PATH`, `ARGO_EVOLUTION_PATH`, `ARGO_PREDICTIONS_PATH`, `ARGO_FRONTIER_SEEN_PATH`, `ARGO_PENDING_EVOLVE_PATH`, `ARGO_PENDING_HEAL_PATH`, `ARGO_WORLD_MODEL_PATH`, `ARGO_LOCAL_SCHED_STATE` — all default to `data/`; see `argo_paths.py`) |

Python 3.11+. Dependencies in `requirements.txt`.

## The One-Line Distinction

> **SEAS asks "What is true?" Argo asks "What should I do next?"**
> SEAS generates knowledge. Argo generates motion.
