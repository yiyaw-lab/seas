# Argo LLM Setup — Observation Generation

Argo V2 Phase A (`src/argo_observe.py`) can generate observations directly by
calling an LLM. This is the only part of Argo wired to a model — it does **not**
select bets, track energy, or send Telegram.

Providers are **not hardcoded**. The model name routes the provider:

| Model name | Provider | Key |
|---|---|---|
| `gpt-*`, `o1*`, `o3*`, `o4*` | OpenAI | `OPENAI_API_KEY` |
| `claude-*` | Anthropic | `ANTHROPIC_API_KEY` |

## What you need

- Python 3.11+
- `python-dotenv` (to load a `.env` file): `pip install python-dotenv`
- The SDK for whichever provider you use:
  - OpenAI: `pip install openai`
  - Anthropic: `pip install anthropic`
- An API key for that provider

## 1. Configure a `.env` file

Create a `.env` in the repo root (it is gitignored — never commit it):

```
# Use whichever provider you want; you don't need both.
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional: pick the model. Its name decides the provider.
ARGO_MODEL=gpt-4o
```

`argo_observe.py` loads `.env` automatically via `python-dotenv`. If
`python-dotenv` isn't installed, it falls back to reading the real environment
(so `export OPENAI_API_KEY=...` also works), and never crashes.

If **no** usable key is found for the selected model(s), the script still runs:
it writes the reusable prompt to `argo/observations/observation_job.md`, leaves
`argo/observations/latest.md` as a placeholder, prints which key to set, and
exits cleanly. It never fabricates observations.

## 2. Run it

```bash
python src/argo_observe.py
```

With a usable key, this will:

1. load `data/signals.json` + `findings/F-001-cognitive-operators.md`,
2. assemble the observation prompt and save it to
   `argo/observations/observation_job.md`,
3. route the model to its provider and call it,
4. save the generated observations to:
   - `argo/observations/latest.md` (most recent), and
   - `argo/observations/YYYY-MM-DD.md` (dated copy, so runs accumulate),
5. print the observations to the terminal.

## 3. Choose the model (ARGO_MODEL)

By default Argo tries `gpt-4.1`, then falls back to `gpt-4o` (both OpenAI).

Set `ARGO_MODEL` to choose a model from **either** provider — the name selects
the provider for you:

```bash
ARGO_MODEL="gpt-4o" python src/argo_observe.py            # OpenAI
ARGO_MODEL="claude-sonnet-4-6" python src/argo_observe.py # Anthropic
```

Or put it in `.env`:

```
ARGO_MODEL=claude-sonnet-4-6
```

`ARGO_MODEL` takes precedence over the defaults. The matching provider key must
be set, and that provider's SDK installed.

### Tool access on the GPT fallback (Claude-outage failover)

In the live chat (`argo_webhook`), the primary brain is Claude and the fallback is
OpenAI. When `WEBHOOK_URL` and `ARGO_MCP_TOKEN` are set (production), the GPT
fallback now reaches Argo's tools through the **OpenAI Responses API remote-MCP
connector** — the same MCP server the Claude path uses. So a Claude outage degrades
to "different brain, same tools" instead of a tool-less brain that bluffs about
opening PRs or reading links. No tool wiring is duplicated; OpenAI runs the loop
server-side, billed only for output tokens. This needs `openai>=1.66` (Responses
API). To make the fallback `gpt-5.5` specifically, set `ARGO_MODEL=gpt-5.5` (note:
that also selects the batch-observe model). With no MCP server configured (local
dev), the GPT path stays a tool-less completion and says so honestly.

### Adding another provider

Providers live in the `PROVIDERS` registry in `src/argo_observe.py`. Each entry
declares how to recognise a model name, which env var holds its key, and how to
call it. Adding a provider is adding one row — nothing else in the flow is
provider-specific.

## Notes

- Output is observations only — by design. Judging them (the Surprise Test) and
  promoting any to insights/bets is a separate, manual step (see
  `ARGO_V2_MIGRATION.md`).
- `latest.md` is overwritten each run; the dated `YYYY-MM-DD.md` copies preserve
  history.
- This script is independent of Argo V1 (`src/argo.py`). Running it does not
  affect weekly bets or energy tracking.
