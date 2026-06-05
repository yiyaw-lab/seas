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
