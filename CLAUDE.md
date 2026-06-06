# CLAUDE.md

Guidance for working in this repo. Behavioral rules first (adapted from the
Karpathy-inspired CLAUDE.md), then project specifics.

## How to work

**1. Think before coding.** Don't assume; don't hide confusion; surface
tradeoffs. State assumptions explicitly and ask when uncertain. If multiple
interpretations exist, present them — don't pick silently. If a simpler approach
exists, say so. If something's unclear, stop and ask before implementing.

**2. Simplicity first.** Minimum code that solves the problem, nothing
speculative. No unrequested features, no premature abstractions, no unasked-for
flexibility, no error handling for impossible cases. If 200 lines could be 50,
write 50.

**3. Surgical changes.** Touch only what the task requires. Don't refactor or
reformat working code that's unrelated. Match existing style. Every changed line
should trace to the request.

**4. Verify.** Define what "done" looks like and check it before claiming
success — compile, run, test. Report failures honestly with the output; never
say something works that you haven't run.

## This project

**What it is.** Two systems: **SEAS** (research engine — "what is true?",
signals → opportunities → findings → theories) and **Argo** (decision/insight
engine — "what should I do next?", a live agentic Telegram scout). See README.md
and build-log/ for the full picture.

**Stack.** Python 3.11, **standard-library-first** — only the deps in
requirements.txt (openai, anthropic, python-dotenv, certifi, feedparser, flask,
mcp, uvicorn, asgiref, starlette). Don't add dependencies without reason. No
build system; scripts run directly (`python3 src/<x>.py`).

**Verify changes** by compiling (`python3 -m py_compile <files>`) and running the
affected script; for Argo runtime changes, the live bot is on Railway behind a
Telegram webhook.

**Testing.** Tests live in `tests/` (stdlib `unittest`, no new dep). Run with
`PYTHONPATH=src python3 -m unittest discover -s tests` (under `python3` / 3.11, not
the 3.9 `.venv`). They cover the four regressions that kept recurring: scheduler
firing/grace/dedupe (`argo_scheduled`), seen-store dedup and legacy-list migration
(`argo_watch`), the rating prompt and decimal ratings
(`argo_project.project_invite`, `argo_webhook._parse_rating`), and project
re-anchoring / last-shown targeting (`argo_webhook._target_project`,
`_match_existing_project`). Tests stay pure — no network, no LLM, no real
`data/*.json`: override the module-level path constants (`SEEN_PATH`,
`PROJECTS_LOG`, `SCHEDULE_PATH`/`STATE_PATH`) to a tmp dir. Rule: a bug fix in any
of those four areas must add or extend a test that fails before the fix and passes
after.

**Observability.** Operational and error paths use `argo_log.get_logger(__name__)`,
not bare `print` — scheduler firing decisions, seen-store outcomes, Telegram
delivery failures, guard/breaker/budget events. User-facing Telegram text still
goes through `_clean_reply` + `print`/return; logs are for the operator
(Railway/Actions console), level set by `ARGO_LOG_LEVEL` (the format is plain text;
a JSON `Formatter` is a small drop-in in `argo_log.py` if log search ever needs
it). A broad `except Exception` safety net must log the exception
(`exc_info=True`), never swallow it. `/` (health) returns a small JSON payload —
status, time, the last few scheduler fires, signal-store age — from local files
only, no network, so it can never hang.

**Exceptions & module size.** Catch the specific type at I/O, network, and parse
boundaries (`json.JSONDecodeError`/`ValueError`, `urllib.error.*`/`OSError`,
`KeyError`); reserve broad `except Exception` for the outermost net that must not
crash a thread or a chat turn, and make that net log. Target under 500 lines per
module. `argo_mcp_server.py`, `argo_webhook.py`, and `argo_observe.py` exceed that
today — don't split them speculatively, but when one next needs a substantive
change, extract one cohesive seam as part of that work (e.g. the
rating/project-state helpers out of `argo_webhook`).

**Argo output rules** (already enforced in code via `_clean_reply`, keep them):
plain text only — no markdown, no em dashes; sources cited like a human, never
"I used <tool>"; Telegram-friendly.

**Security patterns to preserve.** Web/repo access is allowlist-gated server-side
(never trust the model to self-limit); the `/mcp` endpoint is bearer-auth'd;
secrets come from env/.env and are `.strip()`-ed. Never commit secrets.

**Git.** Commit only when asked. Small, surgical commits; subject + the
`Co-Authored-By` trailer. `data/signals.json`, chat logs, and `COST_PLAYBOOK.md`
are gitignored — don't commit them.

**Cost.** Default model is Sonnet; escalate to Opus only for architecture, a
stalled bug, cross-cutting refactors, or high-stakes work — and flag it when you
do (see memory: model-escalation-nudge).
