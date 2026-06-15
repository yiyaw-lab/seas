# Argo Two-Way Chat (Telegram Webhook) Setup

This makes Argo a real two-way chatbot: you message the bot, Argo replies via an
LLM (with short conversation memory). A bare `1-10` is still recorded as a
project energy rating.

Components:
- `src/argo_webhook.py` — the Flask server (receives Telegram POSTs, replies).
- `src/set_webhook.py` — register / inspect / delete the webhook URL.

## Requirements

- The same secrets the rest of Argo uses, in `.env` or the environment:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY` (or
  `ANTHROPIC_API_KEY`), optionally `ARGO_MODEL`.
- Deps: `pip install -r requirements.txt` (adds `flask`).
- **A public HTTPS URL** — Telegram will not deliver to `localhost`. Use a
  tunnel (testing) or a host (always-on).

> Note: a webhook and `getUpdates` polling are mutually exclusive. Setting a
> webhook turns off the polling used by `src/argo_rate.py` — ratings then happen
> inside the chat instead.

## Option A — Test today with a tunnel

1. Run the server locally:
   ```bash
   python src/argo_webhook.py        # listens on :8080
   ```
2. In another terminal, expose it (pick one):
   ```bash
   ngrok http 8080                   # or:
   cloudflared tunnel --url http://localhost:8080
   ```
   Copy the public `https://...` URL it prints.
3. Register the webhook (append `/webhook`):
   ```bash
   python src/set_webhook.py https://YOUR-TUNNEL-URL/webhook
   ```
4. Message your bot on Telegram. Argo replies. ✅

Argo only responds while both the server and the tunnel are running.

## Option B — Always-on (Railway — recommended)

The repo is deploy-ready for Railway: `Procfile` (`web: python src/argo_webhook.py`),
`runtime.txt` (Python 3.11), and `requirements.txt` are all present, and the
server binds `$PORT` / `0.0.0.0` automatically.

1. Go to https://railway.app → **New Project → Deploy from GitHub repo** →
   pick `yiyaw-lab/seas`. Railway detects Python and runs the `Procfile`.
2. In the service → **Variables**, add (same names as `.env`):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `OPENAI_API_KEY`  (or `ANTHROPIC_API_KEY`)
   - `ARGO_MODEL`  (optional)
   - `TELEGRAM_WEBHOOK_SECRET`  (optional but recommended — see below)
   - `ARGO_CHAT_LOG` = `/data/argo_chat.json`  (so chat memory lands on the
     persistent volume below — see "Persistent chat memory")
   - `WEBHOOK_URL` = `https://YOUR-RAILWAY-DOMAIN`  (base URL, no `/webhook`) —
     the server self-registers the webhook on every startup, so a domain change
     or bot-token rotation can't silently leave the bot deaf. Also used to build
     the MCP server URL the chat hands to Claude.
   - `ARGO_MCP_TOKEN` = `<long random string>`  (enables Argo's tools) — bearer
     token guarding the `/mcp` endpoint. When this AND `WEBHOOK_URL` are set,
     chat gains tool use (web fetch, etc.); unset = plain chat, no tools. Argo
     passes it to itself via the connector, so it never needs to be shared.
   - `ANTHROPIC_API_KEY` = `sk-ant-...`  (required for Claude chat + MCP; without
     it the bot falls back to gpt-4o and tools are off).
   Paste each value as a single line (no trailing newline).
3. Under **Settings → Networking**, click **Generate Domain** to get a public
   HTTPS URL like `https://seas-production.up.railway.app`.
4. With `WEBHOOK_URL` set (step 2), the server registers itself on startup —
   no manual step. Otherwise register once:
   ```bash
   python src/set_webhook.py https://YOUR-RAILWAY-URL/webhook
   ```
5. Text your bot on Telegram. Argo replies, 24/7. ✅

> Gotcha: changing the Railway domain OR rotating the Telegram bot token wipes
> the webhook (the bot goes silent with no error). With `WEBHOOK_URL` set, the
> next redeploy fixes it automatically; otherwise re-run `set_webhook.py`.

Health check: visit `https://YOUR-RAILWAY-URL/` — it returns a small JSON status
(scheduler fires, signal-store age, open incidents).

Verify a rotated secret actually deployed (operator-only): pass the
`ARGO_MCP_TOKEN` bearer and the payload adds a `config` section with value-free
fingerprints (length + `sha256[:8]`, never the secret):

```bash
curl -s -H "Authorization: Bearer $ARGO_MCP_TOKEN" https://YOUR-RAILWAY-URL/ \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["config"])'
# compare a token's sha8 to your local copy:
printf %s "$GITHUB_TOKEN" | sha256sum   # first 8 hex must match config.GITHUB_TOKEN.sha8
```

If the fingerprints differ, the running process is holding a stale value — redeploy
so the new env var takes effect. A `has_surrounding_whitespace` flag means the
stored value has a stray newline/space.

### Other hosts (Render / Fly / VPS)

Same idea: deploy the repo, start command `python src/argo_webhook.py`, set the
secrets in the host's secret manager (not `.env`), expose HTTPS, then run
`set_webhook.py` against the public URL. Render's free tier sleeps on
inactivity (slow first reply); Railway/Fly stay warm.

## Securing the endpoint (recommended)

Set a shared secret so only Telegram can POST to you:

```bash
# pick any random string; set it BOTH on the server env and before set_webhook
export TELEGRAM_WEBHOOK_SECRET="some-long-random-string"
python src/set_webhook.py https://YOUR-URL/webhook   # registers the secret
```

The server checks the `X-Telegram-Bot-Api-Secret-Token` header and rejects
mismatches with 403.

## Managing the webhook

```bash
python src/set_webhook.py --info      # show current webhook + pending count
python src/set_webhook.py --delete    # remove it (returns to getUpdates polling)
```

## Persistent chat memory (Railway volume)

Conversation history is written to an append-only log (`ARGO_CHAT_LOG`, default
`data/argo_chat.json`) — it's both the LLM's short-term memory and durable data
for later analysis. Railway's container disk is wiped on every redeploy, so put
this log on a **volume**:

1. In the service → **Settings → Volumes** (or the **Data** tab) → **Add Volume**.
2. Set the mount path to `/data`.
3. Ensure the `ARGO_CHAT_LOG=/data/argo_chat.json` variable (step 2 above) points
   inside that mount.

Now memory survives redeploys and restarts. To analyse it, pull the file from
the volume (Railway shell/CLI) — it's a plain JSON array of
`{ts, chat_id, role, text}` turns.

Without a volume the bot still works and remembers within a single deploy, but
history is lost on each redeploy.

## How it behaves

- Any text → LLM reply in Argo's scout voice, with the last ~12 turns of memory
  read from the persisted log (survives restarts when on a volume).
- A bare `1-10` → recorded as energy on the latest unrated project in
  `data/argo_projects.json` (same as the old rating flow).
- The weekly project push (`argo_project.py`) is unchanged and independent.
