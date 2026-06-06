# SEAS + Argo

Two complementary systems for working at the frontier of AI:

- **SEAS** — a **research engine**. Turns frontier signals into knowledge.
  Asks: *"What is true?"*
- **Argo** — a **decision + insight engine**. Turns knowledge into action.
  Asks: *"What should I do next?"* and *"What is everyone missing?"*

SEAS generates understanding. Argo generates motion.

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

Run synthesis: `python src/seas_finding.py` (or the `SEAS Findings` workflow).
Findings persist as JSON in `findings/` with a `runs/<id>/` source bundle; beliefs
in `data/world_model.json`; dead ends in `data/probes.json`. Needs
`FIRECRAWL_API_KEY` for topical related-source search (else it honestly probes
`premature`).

> The legacy `Signal → Opportunity → Experiment` scripts and prose F-001 are
> retained but superseded by the V3 gate-based pipeline. Inventory in
> [AUDIT.md](docs/audits/AUDIT.md); full session history in [build-log/](build-log/).

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
  Runs on a schedule defined in `data/schedule.json`.
- **Self-status** — `get_webhook_health`, `get_latest_project`,
  `get_signal_freshness`: Argo reports its own health honestly.
- **Self-heal** — `reregister_webhook` + `refetch_signals`, gated by
  `ARGO_HEAL_LEVEL`. L0 = report-only (default); L1 = proposes a fix, executes
  only on your Telegram "CONFIRM" reply.
- **Self-create** — `propose_change` opens a GitHub PR so Argo can draft a new
  capability (feed, tool, schedule) for your review. It never self-merges.
  New feeds and schedules are data Argo can propose; workflows require a human.

### The closed loop

```
Argo spots a gap → verify_feed / read repo → propose_change → PR → you review
→ you merge → Railway redeploys → Argo gains the capability
```

Generator (Argo) / reviewer / merger (you) are three separate roles, with the
merge as the safety gate. Proven in practice: Argo drafted **PR #1** (Add arXiv
cs.SE feed), we reviewed and merged it.

### Design docs

- [docs/architecture/ARGO_ARCHITECTURE.md](docs/architecture/ARGO_ARCHITECTURE.md) — V1 decision engine (frozen)
- [docs/architecture/ARGO_V2.md](docs/architecture/ARGO_V2.md) — V2 insight engine (approved design)
- [docs/plans/ARGO_V2_MIGRATION.md](docs/plans/ARGO_V2_MIGRATION.md) — V1 → V2 path
- [docs/plans/PHASE_1_PLAN.md](docs/plans/PHASE_1_PLAN.md) — codebase cleanup plan
- [docs/audits/AUDIT.md](docs/audits/AUDIT.md) — codebase audit
- [build-log/](build-log/) — dated session logs (newest first)
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
  argo_watch.py              ← tripwire: proactive frontier alerts
  argo_scheduled.py          ← hourly schedule runner (reads data/schedule.json)
  argo_rate.py               ← read Telegram replies for energy ratings
  fetch_signals.py           ← RSS ingestion (reads data/feeds.json)
  argo.py                    ← V1 weekly bet + energy (interactive)
  seas.py                    ← SEAS orchestrator
  send_telegram.py, set_webhook.py, seas_demo.py  ← delivery + utilities
  (older SEAS scripts — see docs/audits/AUDIT.md)

data/
  feeds.json                 ← approved signal sources (data; Argo can propose edits)
  schedule.json              ← delivery schedules (data; Argo can propose edits)
  argo_projects.json         ← V2 projects + energy ratings
  argo_bets.json             ← V1 bet + energy log
  argo_seen.json             ← tripwire dedup store
  signals.json               ← live signal cache (gitignored; refreshed each run)
  argo_chat.json             ← chat memory (Railway volume; gitignored locally)

build-log/    dated session logs (2026-06-04 sessions 1 + 2)
docs/         TELEGRAM_SETUP.md, ARGO_LLM_SETUP.md, ARGO_WEBHOOK_SETUP.md
demo/         generated demo report + weekly message
findings/     F-001 cognitive operators (canonical finding)
experiments/  SEAS-00x experiment cards
```

## Automation

| Workflow | Schedule | What it does |
|---|---|---|
| `argo-schedule.yml` | Hourly (UTC) | Runs `argo_scheduled.py`; fires deliveries due this hour per `data/schedule.json`. Single scheduler — add/change deliveries by editing the data file. |
| `seas-friday-telegram.yml` | Manual only | On-demand fresh project generation + Telegram send. |
| `argo-watch.yml` | Manual only | On-demand tripwire sweep. |
| `seas-weekly.yml` | Mondays 15:00 UTC | Runs `seas.py`, commits `data/` + `runs/`. |

> Scheduling is now centralised in `data/schedule.json`. The hourly runner fires
> whatever is due — new schedules are data edits, not workflow edits.

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

Python 3.11+. Dependencies in `requirements.txt`.

## The One-Line Distinction

> **SEAS asks "What is true?" Argo asks "What should I do next?"**
> SEAS generates knowledge. Argo generates motion.
