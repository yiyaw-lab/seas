"""
Argo V2 — Phase G: the tripwire (proactive frontier alerts).

Flips Argo from reactive to proactive. On a schedule (GitHub Actions cron,
~3-4x/day) this watcher:
  1. fetches current items from the frontier feeds (fetch_signals.FEEDS),
  2. dedups against a seen-store (data/argo_seen.json) so only genuinely NEW
     items are considered,
  3. runs an LLM judge: "would a frontier builder want to know this today?",
     keeping only real launches/models/tools/capabilities (not routine papers),
  4. texts Yiya up to MAX_ALERTS short alerts + links, in Argo's plain-text voice,
  5. records everything seen (whether alerted or not) so it won't repeat.

Runs as a batch job, NOT in the webhook, so it can't slow chat. Reuses
fetch_signals (fetch/parse), argo_observe (LLM call + provider routing),
send_telegram.send_message (delivery), and argo_webhook._clean_reply (voice).

Run:  python src/argo_watch.py            (fetch, judge, send, record)
      python src/argo_watch.py --no-send  (dry run: print what it would alert)
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import argo_observe as observe
import fetch_signals
import send_telegram
from argo_webhook import _clean_reply

SEEN_PATH = ROOT / "data" / "argo_seen.json"
PER_FEED = 10          # consider this many recent items per feed
MAX_ALERTS = 3         # cap alerts per run so the phone doesn't blow up
SEEN_CAP = 2000        # keep the seen-store bounded


def _item_id(item):
    """Stable identity for dedup: prefer the link, fall back to title."""
    return (item.get("link") or item.get("title") or "").strip().lower()


def load_seen():
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def save_seen(seen):
    SEEN_PATH.write_text(json.dumps(seen[-SEEN_CAP:], indent=2) + "\n")


def collect_new(seen_ids):
    """Fetch all feeds, return new (unseen) items as a flat list."""
    new = []
    for label, url in fetch_signals.FEEDS:
        for item in fetch_signals.fetch_feed(label, url)[:PER_FEED]:
            if _item_id(item) and _item_id(item) not in seen_ids:
                new.append(item)
    return new


JUDGE_INSTRUCTIONS = """You are Argo, a frontier scout. Below are NEW items that
just appeared on AI frontier feeds. Pick ONLY the ones a frontier builder would
genuinely want to know about TODAY: real launches, new models, new tools/products,
notable capability jumps, or major lab announcements. DROP routine papers,
incremental research, listicles, and noise. Most items should be dropped.

For each item you keep, write ONE short plain-text line (no markdown) saying what
it is and why it matters, then the link on its own. Keep at most {max_alerts}.
If nothing clears the bar, output exactly: NONE

Format per kept item:
<one sharp sentence>
<link>

Items:
{items}
"""


def judge(new_items):
    """LLM judge -> list of {text, link} alerts (<= MAX_ALERTS). [] if none."""
    if not new_items:
        return []

    listing = "\n\n".join(
        f"- {it['title']}\n  {it.get('summary','')[:200]}\n  {it.get('link','')}"
        for it in new_items[:60]  # cap prompt size
    )
    prompt = JUDGE_INSTRUCTIONS.format(max_alerts=MAX_ALERTS, items=listing)

    # Use the configured chat model (Claude if available, else gpt-4o fallback).
    model = next(
        (m for m in [os.environ.get("ARGO_CHAT_MODEL", "claude-sonnet-4-6")]
         + observe.resolve_models()
         if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])),
        None,
    )
    if model is None:
        print("No API key available for the judge — skipping.")
        return []

    if observe.provider_for(model)["name"] == "anthropic":
        raw = observe.chat_with_mcp(
            "You are Argo, a terse frontier scout.",
            [{"role": "user", "content": prompt}], model,
        )
    else:
        raw = observe.generate_observations(prompt, model)

    raw = _clean_reply(raw.strip())
    if not raw or raw.strip().upper() == "NONE":
        return []

    # Parse "<sentence>\n<link>" pairs out of the model output.
    alerts = []
    block = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            if block:
                alerts.append(" ".join(block)); block = []
            continue
        block.append(line)
    if block:
        alerts.append(" ".join(block))
    return alerts[:MAX_ALERTS]


def main():
    no_send = "--no-send" in sys.argv

    seen = load_seen()
    seen_ids = set(seen)

    new_items = collect_new(seen_ids)
    print(f"\n📡 Argo Watch — {len(new_items)} new items since last check")

    alerts = judge(new_items)

    if not alerts:
        print("Nothing cleared the frontier-builder bar this run.")
    else:
        print(f"\n{len(alerts)} alert(s):")
        for a in alerts:
            print(f"  • {a}")
            if not no_send:
                send_telegram.send_message(f"🛰️ Argo spotted something:\n\n{a}")

    # Record ALL new items as seen (alerted or not) so we don't repeat them.
    if not no_send:
        seen.extend(_item_id(it) for it in new_items if _item_id(it))
        save_seen(seen)
        print(f"\nRecorded {len(new_items)} items as seen "
              f"({SEEN_PATH.relative_to(ROOT)}).")
    else:
        print("\n(--no-send: nothing sent, seen-store NOT updated)")

    print("\n✅ Watch complete.\n")


if __name__ == "__main__":
    main()
