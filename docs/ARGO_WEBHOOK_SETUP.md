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

## Option B — Always-on (Railway / Render / Fly / VPS)

1. Deploy this repo to your host. Start command:
   ```
   python src/argo_webhook.py
   ```
   The server reads `PORT` from the environment (hosts set this automatically).
2. Set the secrets on the host (same names as above). Do **not** rely on `.env`
   in production — use the host's secret manager.
3. Register the webhook to your host's public URL:
   ```bash
   python src/set_webhook.py https://your-app.example.com/webhook
   ```

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

## How it behaves

- Any text → LLM reply in Argo's scout voice, with the last ~12 turns of memory
  (memory is in-process; it resets if the server restarts — a persistent store
  can replace it later).
- A bare `1-10` → recorded as energy on the latest unrated project in
  `data/argo_projects.json` (same as the old rating flow).
- The weekly project push (`argo_project.py`) is unchanged and independent.
