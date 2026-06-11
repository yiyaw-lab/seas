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
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESSAGE_PATH = ROOT / "demo" / "weekly_project_message.md"

import argo_http
from argo_log import get_logger

log = get_logger(__name__)

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


def try_send_message(text):
    """Send `text` and return True on success, False on any failure — WITHOUT
    exiting. Use inside the server (webhook/MCP tools), where send_message's
    sys.exit(1) on failure would raise SystemExit, escape `except Exception`, and
    die silently in a worker thread (the 'replied sent but nothing arrived' bug).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not (text and text.strip()):
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        API_URL.format(token=token), data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    ctx = argo_http.tls_context()
    try:
        with urllib.request.urlopen(request, timeout=30, context=ctx) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        log.error("telegram sendMessage failed: %s: %s", type(exc).__name__, exc)
        try:  # surface repeated delivery failures to the diagnostic loop
            import argo_incidents
            argo_incidents.record_incident(
                "delivery_failure", f"sendMessage {type(exc).__name__}: {exc}", str(exc))
        except Exception:
            pass
        return False


def send_message(text):
    """Send `text` to the configured Telegram chat.

    Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment (.env is
    loaded above). Exits via fail() on missing creds or delivery errors.
    Importable by other senders (e.g. argo_project.py).
    """
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

    if not text or not text.strip():
        fail("Refusing to send an empty message.")

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")

    request = urllib.request.Request(
        API_URL.format(token=token),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print("📨 Sending message to Telegram...")

    ssl_context = argo_http.tls_context()

    try:
        with urllib.request.urlopen(
            request, timeout=30, context=ssl_context
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        log.error("telegram sendMessage HTTP %s: %s", error.code, detail)
        fail(f"Telegram API returned HTTP {error.code}: {detail}")
    except urllib.error.URLError as error:
        log.error("telegram sendMessage unreachable: %s", error.reason)
        fail(f"Could not reach Telegram API: {error.reason}")

    if not body.get("ok"):
        fail(f"Telegram API rejected the request: {body}")

    print("✅ Message delivered to Telegram.")


def send_document(filename, content, caption=""):
    """Upload `content` (str) as a file named `filename` to the chat, with an
    optional short caption. Used to deliver a full project proposal as an
    attachment while a one-line pitch goes as a normal message.

    Returns True on success, False on any failure — unlike send_message it does
    NOT exit, so a caller can fall back to sending the proposal as text. Pure
    stdlib multipart/form-data (no requests dependency)."""
    import uuid

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not content:
        return False

    boundary = uuid.uuid4().hex
    data = content.encode("utf-8")

    def part(headers, payload):
        return (f"--{boundary}\r\n{headers}\r\n\r\n".encode("utf-8")
                + payload + b"\r\n")

    body = part('Content-Disposition: form-data; name="chat_id"',
                str(chat_id).encode("utf-8"))
    if caption:
        body += part('Content-Disposition: form-data; name="caption"',
                     caption.encode("utf-8"))
    body += part(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'
        '\r\nContent-Type: text/markdown',
        data,
    )
    body += f"--{boundary}--\r\n".encode("utf-8")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    ctx = argo_http.tls_context()
    try:
        with urllib.request.urlopen(request, timeout=30, context=ctx) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
        return bool(ok)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        log.error("telegram sendDocument failed: %s: %s", type(exc).__name__, exc)
        return False


def main():
    if not MESSAGE_PATH.exists():
        fail(
            f"Message file not found: {MESSAGE_PATH.relative_to(ROOT)}. "
            "Run `python src/seas_demo.py` first."
        )

    text = MESSAGE_PATH.read_text().strip()
    if not text:
        fail(f"Message file is empty: {MESSAGE_PATH.relative_to(ROOT)}.")

    send_message(text)


if __name__ == "__main__":
    main()
