"""
Argo — two-way Telegram chat (webhook server).

Telegram pushes each message you send the bot to this server's /webhook
endpoint; Argo runs it through an LLM and replies. This is the "real bot"
architecture: instant, no polling.

Conversation history is persisted to an append-only JSON log (ARGO_CHAT_LOG,
default data/argo_chat.json) — it's both the LLM's short-term memory and durable
data for later analysis. On Railway, point ARGO_CHAT_LOG at a mounted volume so
it survives redeploys.

Host-agnostic: it's a plain WSGI/Flask app, so it runs behind any public HTTPS
URL — a tunnel (ngrok / cloudflared) for testing, or a host (Railway / Render /
Fly) for always-on. See docs/ARGO_WEBHOOK_SETUP.md.

Reuses, not duplicates:
  - argo_observe: provider routing + model call + .env/ARGO_MODEL config;
  - send_telegram.send_message: outbound delivery (.env creds + certifi TLS).

Special messages still work: a bare 1-10 is recorded as an energy rating on the
latest unrated project (same store as argo_rate.py), so the rating loop keeps
working inside the chat.

Does NOT touch Argo V1 (argo.py) or generation. Webhook and getUpdates are
mutually exclusive in Telegram — running this means argo_rate.py polling is off
(ratings now happen here instead).

Run locally:  python src/argo_webhook.py   (then expose :8080 via a tunnel)
Register URL: python src/set_webhook.py https://your-public-url/webhook
"""

import json
import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(ROOT / ".env")
except ImportError:
    ROOT = Path(__file__).resolve().parent.parent

import argo_observe as observe
import send_telegram

PROJECTS_LOG = ROOT / "data" / "argo_projects.json"

# Optional shared-secret check: Telegram sends this header if you set it on the
# webhook (set_webhook.py does). Blocks randoms POSTing to your endpoint.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

SYSTEM_PROMPT = (
    "You are Argo, a frontier scout and decision engine. You talk with Yiya "
    "about frontier AI, the projects you suggest, and what's worth building. "
    "Be curious, observant, calm — a scout who found something interesting. "
    "Lead with insight, keep replies short and conversational (this is a text "
    "chat, not an essay). You are not a generic assistant."
)

# Persisted, append-only chat log. This is durable conversation data (for
# analysis) AND the source of the LLM's short-term memory. On Railway, point
# ARGO_CHAT_LOG at a mounted volume (e.g. /data/argo_chat.json) so it survives
# redeploys; locally it defaults to data/argo_chat.json.
HISTORY_TURNS = 12  # how many recent turns to feed the model as context
CHAT_LOG_PATH = Path(
    os.environ.get("ARGO_CHAT_LOG", str(ROOT / "data" / "argo_chat.json"))
)


def _append_turn(chat_id, role, text):
    """Append one turn to the durable log (creates the file/dir if needed)."""
    from datetime import datetime, timezone

    CHAT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if CHAT_LOG_PATH.exists():
        try:
            log = json.loads(CHAT_LOG_PATH.read_text())
        except (json.JSONDecodeError, ValueError):
            log = []  # never lose a reply over a corrupt read
    log.append({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chat_id": chat_id,
        "role": role,
        "text": text,
    })
    CHAT_LOG_PATH.write_text(json.dumps(log, indent=2) + "\n")


def _recent_turns(chat_id, n=HISTORY_TURNS):
    """Read the last n turns for this chat from the durable log."""
    if not CHAT_LOG_PATH.exists():
        return []
    try:
        log = json.loads(CHAT_LOG_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return []
    turns = [t for t in log if t.get("chat_id") == chat_id]
    return turns[-n:]


def _llm_reply(chat_id, user_text):
    """Generate Argo's reply with short conversation memory."""
    models = observe.resolve_models()
    runnable = [
        m for m in models
        if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])
    ]
    if not runnable:
        return "(Argo can't think right now — no API key configured.)"

    # Build a single prompt: system + recent turns (from the durable log) + this
    # message. We reuse generate_observations (a generic "send prompt -> text"
    # call), so memory is folded into the prompt rather than passed as messages.
    hist = _recent_turns(chat_id)
    convo = "\n".join(f"{t['role']}: {t['text']}" for t in hist)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Conversation so far:\n{convo}\n\n"
        f"Yiya: {user_text}\n\nArgo:"
    )

    last_error = None
    for model in runnable:
        try:
            reply = observe.generate_observations(prompt, model).strip()
            # Persist both turns so memory survives restarts and is analysable.
            _append_turn(chat_id, "Yiya", user_text)
            _append_turn(chat_id, "Argo", reply)
            return reply
        except Exception as exc:
            last_error = exc
    return f"(Argo hit an error reaching the model: {last_error})"


def _parse_rating(text):
    m = re.match(r"\s*(10|[1-9])\s*$", (text or "").strip())
    return int(m.group(1)) if m else None


def _record_rating(value):
    """Apply a 1-10 to the latest unrated project. Returns a status string."""
    if not PROJECTS_LOG.exists():
        return None
    log = json.loads(PROJECTS_LOG.read_text())
    target = next((e for e in reversed(log) if e.get("energy") is None), None)
    if target is None:
        return None
    target["energy"] = value
    from datetime import datetime, timezone
    target["rated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    PROJECTS_LOG.write_text(json.dumps(log, indent=2) + "\n")
    return f"Logged energy {value}/10 for {target['id']}. 👍"


def handle_update(update):
    """Process one Telegram update dict; send a reply. Pure-ish + testable."""
    msg = update.get("message") or update.get("channel_post") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "")
    if chat_id is None or not text:
        return

    # A bare 1-10 is a project rating (keeps the energy loop working in chat).
    rating = _parse_rating(text)
    if rating is not None:
        status = _record_rating(rating)
        send_telegram.send_message(
            status or f"Got {rating}/10, but there's no unrated project to log it against."
        )
        return

    reply = _llm_reply(chat_id, text)
    send_telegram.send_message(reply)


def create_app():
    from flask import Flask, request

    app = Flask(__name__)

    @app.get("/")
    def health():
        return "Argo webhook is up.", 200

    @app.post("/webhook")
    def webhook():
        if WEBHOOK_SECRET:
            sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if sent != WEBHOOK_SECRET:
                return "forbidden", 403
        update = request.get_json(force=True, silent=True) or {}
        try:
            handle_update(update)
        except Exception as exc:  # never 500 back to Telegram (it retries)
            print(f"handle_update error: {exc}")
        return "ok", 200

    return app


def main():
    app = create_app()
    port = int(os.environ.get("PORT", "8080"))
    print(f"🛰️  Argo webhook listening on :{port} (POST /webhook)")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
