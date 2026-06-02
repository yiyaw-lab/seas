"""
Send the SEAS weekly project message to Telegram.

Reads demo/weekly_project_message.md and delivers it via the Telegram Bot API
sendMessage method. Credentials come from a .env file (via python-dotenv) or
the environment:

    TELEGRAM_BOT_TOKEN   bot token from BotFather
    TELEGRAM_CHAT_ID     chat id to send to

Minimal proof of concept. Standalone: imports nothing from seas.py and does
not modify any existing file. Run with:  python src/send_telegram.py
"""

import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESSAGE_PATH = ROOT / "demo" / "weekly_project_message.md"

# Load .env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) if python-dotenv is
# installed; otherwise fall back to the real environment. Never hard-depends
# on dotenv.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def fail(message):
    print(f"❌ {message}")
    sys.exit(1)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        fail(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them before running (see docs/TELEGRAM_SETUP.md)."
        )

    if not MESSAGE_PATH.exists():
        fail(
            f"Message file not found: {MESSAGE_PATH.relative_to(ROOT)}. "
            "Run `python src/seas_demo.py` first."
        )

    text = MESSAGE_PATH.read_text().strip()
    if not text:
        fail(f"Message file is empty: {MESSAGE_PATH.relative_to(ROOT)}.")

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")

    request = urllib.request.Request(
        API_URL.format(token=token),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print("📨 Sending SEAS weekly message to Telegram...")

    # Verify TLS against certifi's CA bundle when available (some Python builds,
    # notably macOS framework Python, ship without trusted roots). Falls back to
    # the system default context — verification stays ON either way.
    try:
        import certifi

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(
            request, timeout=30, context=ssl_context
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        fail(f"Telegram API returned HTTP {error.code}: {detail}")
    except urllib.error.URLError as error:
        fail(f"Could not reach Telegram API: {error.reason}")

    if not body.get("ok"):
        fail(f"Telegram API rejected the request: {body}")

    print("✅ Message delivered to Telegram.")


if __name__ == "__main__":
    main()
