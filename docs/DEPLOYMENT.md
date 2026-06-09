# Deployment & operations

Argo runs as a single always-on web service (the Telegram webhook + MCP server),
plus scheduled GitHub Actions jobs for the proactive work. SEAS runs on demand.

## The web service (Railway)

- **Entry:** `Procfile` → `web: python src/argo_webhook.py` (Flask + the FastMCP
  server mounted at `/mcp`, served via ASGI/uvicorn).
- **Runtime:** `runtime.txt` → `python-3.11`.
- **Self-registering webhook:** on boot, if `WEBHOOK_URL` is set, the service
  re-points Telegram's webhook at itself. A domain or bot-token change can't
  silently leave the bot deaf.
- **Health:** `GET /` returns JSON from local files only — `status`, UTC `time`,
  the last few scheduler fires, signal-store age, and a performance snapshot
  (projects rated, mean energy, tripwire seen/settled). It never makes a network
  call, so it can't hang. Point an uptime check here.

## Scheduled work (GitHub Actions)

| Workflow | Trigger | What it does |
|---|---|---|
| `argo-schedule.yml` | hourly (UTC) | Runs `argo_scheduled.py` with a 3-hour grace window; fires whatever is due in `data/schedule.json` (project Fridays, tripwire 14/19/00 UTC, weekly self-reflection). |
| `argo-watch.yml` | manual | One-off tripwire sweep (fallback). |
| `seas-friday-telegram.yml` | manual | One-off project generation + send (fallback). |
| `seas-findings.yml` | manual | Runs the SEAS V3 pipeline; commits findings/beliefs/probes + source bundles. |
| `tests.yml` | push / PR | Runs the unit suite (read-only, never commits). |

**Why the grace window:** GitHub's hourly cron drifts past the exact-hour mark and
would skip a 15:00-only job. The scheduler treats anything within 3 hours of the
target hour as due, then dedupes per day so it fires once. (This is a *feature*:
it makes cron-on-a-budget reliable.)

**SEAS cadence is manual at launch.** `seas-findings.yml` is `workflow_dispatch`
only — we don't auto-commit findings to the public repo on a cron until the
cadence is proven. Move it to `data/schedule.json` (a data edit, no workflow
change) when ready.

## Environment variables

See [ARGO_LLM_SETUP.md](ARGO_LLM_SETUP.md) and [ARGO_WEBHOOK_SETUP.md](ARGO_WEBHOOK_SETUP.md)
for the full list. The ones that gate behavior:

| Variable | Effect if unset |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | No model → SEAS can't synthesize, Argo can't chat. At least one required. |
| `FIRECRAWL_API_KEY` | SEAS mostly probes `premature` (see [FIRECRAWL_SETUP.md](FIRECRAWL_SETUP.md)). |
| `WEBHOOK_URL` | Webhook self-registration is skipped (register manually). |
| `ARGO_MCP_TOKEN` | The `/mcp` endpoint has no bearer auth — set it. |
| `ARGO_PROPOSE_REPO` | Defaults to a non-real placeholder so a fork can't PR upstream. Set to your `owner/repo` for self-create. |
| `ARGO_HEAL_LEVEL` | Defaults to `L0` (report-only). `L1` enables confirm-in-chat self-heal. |
| `ARGO_CHAT_LOG` / `ARGO_SELF_PATH` / `ARGO_TASTE_PATH` | Point these at a mounted volume so chat memory, self-model, and taste survive redeploys. |

## Persistence note

Runtime state (`data/argo_chat.json`, `argo_self.json`, `taste_signals.json`,
`signals.json`, scheduler state, budget counter) is gitignored and lives on the
Railway volume, not in git. The repo carries only durable knowledge: committed
findings, the world model, and probes.
