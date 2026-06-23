"""
Register / inspect / delete the Argo Telegram webhook.

Telegram delivers messages either via getUpdates (polling) OR a webhook — not
both. Setting a webhook here turns off the polling used by argo_rate.py; chat +
ratings then happen through argo_webhook.py instead.

Usage:
  python src/set_webhook.py https://your-public-url/webhook   # register
  python src/set_webhook.py --info                            # show current
  python src/set_webhook.py --delete                          # remove (back to polling)

Credentials from .env (TELEGRAM_BOT_TOKEN) or the environment. If
TELEGRAM_WEBHOOK_SECRET is set, it is registered as the secret token so the
server can verify inbound requests.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import argo_http

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _api(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20, context=argo_http.tls_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set (see docs/TELEGRAM_SETUP.md).")
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--info":
        print(json.dumps(_api(token, "getWebhookInfo")["result"], indent=2))
        return

    if args[0] == "--delete":
        res = _api(token, "deleteWebhook", {"drop_pending_updates": "false"})
        print("✅ Webhook deleted (back to getUpdates polling)." if res.get("ok")
              else f"❌ {res}")
        return

    url = args[0]
    if not url.startswith("https://"):
        print("❌ Webhook URL must be public HTTPS.")
        sys.exit(1)

    params = {"url": url}
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if secret:
        params["secret_token"] = secret

    res = _api(token, "setWebhook", params)
    if res.get("ok"):
        print(f"✅ Webhook set to {url}")
        if secret:
            print("   (secret token registered — set the same "
                  "TELEGRAM_WEBHOOK_SECRET on the server)")
    else:
        print(f"❌ {res}")


if __name__ == "__main__":
    main()
