"""
Argo V2 — rate projects by replying on Telegram.

Closes the energy loop for V2: argo_project.py sends a project and asks
"Reply 1-10: how much do you want to build this?". This script reads those
replies via the Telegram Bot API getUpdates method and records each number as
the energy rating on the most recent unrated project in data/argo_projects.json.

Energy (how much you want to build it) is Argo's optimization target — see
ARGO_V2.md. This is the deferred "Telegram read-side" from ARGO_V2_MIGRATION.md,
now built because ratings happen on the phone.

How it works:
  - getUpdates returns messages sent TO the bot (your replies);
  - we keep an offset (argo/telegram_offset.json) so each update is read once;
  - the newest 1-10 number found is applied to the latest unrated project.

Credentials come from .env (via python-dotenv) or the environment:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Standalone. Does not touch Argo V1 (argo.py) or generation. No LLM call.
Run with:  python src/argo_rate.py
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import argo_http
import argo_store

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_LOG = ROOT / "data" / "argo_projects.json"
OFFSET_PATH = ROOT / "argo" / "telegram_offset.json"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

API_URL = "https://api.telegram.org/bot{token}/getUpdates"


def fail(message):
    print(f"❌ {message}")
    sys.exit(1)


def _ssl_context():
    return argo_http.tls_context()


def get_updates(token, offset):
    url = API_URL.format(token=token)
    if offset is not None:
        url += f"?offset={offset}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        fail(f"Telegram getUpdates HTTP {e.code}: {e.read().decode('utf-8','replace')}")
    except urllib.error.URLError as e:
        fail(f"Could not reach Telegram API: {e.reason}")
    if not body.get("ok"):
        fail(f"Telegram rejected getUpdates: {body}")
    return body["result"]


def load_offset():
    if OFFSET_PATH.exists():
        return json.loads(OFFSET_PATH.read_text()).get("offset")
    return None


def save_offset(offset):
    OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(json.dumps({"offset": offset}) + "\n")


def parse_rating(text):
    """Return an int 1-10 if the message is (or starts with) a 1-10 number."""
    if not text:
        return None
    m = re.match(r"\s*(10|[1-9])\b", text.strip())
    return int(m.group(1)) if m else None


def latest_unrated(log):
    for entry in reversed(log):
        if entry.get("energy") is None:
            return entry
    return None


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        fail("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set "
             "(see docs/TELEGRAM_SETUP.md).")

    if not PROJECTS_LOG.exists():
        fail(f"No project log at {PROJECTS_LOG.relative_to(ROOT)}. "
             "Run src/argo_project.py first.")

    log = argo_store.load_json(PROJECTS_LOG, [])

    offset = load_offset()
    updates = get_updates(token, offset)

    print("\n⭐ Argo — Rate (read Telegram replies)\n")

    ratings = []
    new_offset = offset
    for upd in updates:
        new_offset = upd["update_id"] + 1
        msg = upd.get("message") or upd.get("channel_post") or {}
        # Only accept replies from your own chat.
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue
        value = parse_rating(msg.get("text", ""))
        if value is not None:
            ratings.append(value)

    if new_offset != offset:
        save_offset(new_offset)

    if not ratings:
        print("No new 1-10 ratings found in Telegram replies.")
        print("Reply to a project message with a number 1-10, then re-run.\n")
        return

    # Apply the newest rating to the latest unrated project.
    target = latest_unrated(log)
    if target is None:
        print(f"Got rating(s) {ratings}, but every logged project is already "
              "rated. Nothing to apply.\n")
        return

    rating = ratings[-1]
    target["energy"] = rating
    target["rated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    argo_store.save_json(PROJECTS_LOG, log)

    if len(ratings) > 1:
        print(f"Read {len(ratings)} ratings {ratings}; applied the newest.")
    print(f"Rated {target['id']} ({target['date']}): energy {rating}/10")
    print(f"Saved: {PROJECTS_LOG.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
