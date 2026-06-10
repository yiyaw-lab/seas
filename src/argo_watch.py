"""
Argo V2 — Phase G: the tripwire (proactive frontier alerts).

Flips Argo from reactive to proactive. On a schedule (GitHub Actions cron,
~3-4x/day) this watcher:
  1. fetches current items from the frontier feeds (fetch_signals.FEEDS),
  2. dedups against a seen-store (data/argo_seen.json) so only genuinely NEW
     items are considered,
  3. runs an LLM judge: "would a frontier builder want to know this today?",
     keeping only real launches/models/tools/capabilities (not routine papers),
  4. texts Yiya the items that clear that bar (no fixed count — strength decides;
     only ALERT_SAFETY_CAP bounds a runaway run), in Argo's plain-text voice,
  5. records everything seen (whether alerted or not) so it won't repeat.

Runs as a batch job, NOT in the webhook, so it can't slow chat. Reuses
fetch_signals (fetch/parse), argo_observe (LLM call + provider routing),
send_telegram.send_message (delivery), and argo_webhook._clean_reply (voice).

Run:  python src/argo_watch.py            (fetch, judge, send, record)
      python src/argo_watch.py --no-send  (dry run: print what it would alert)
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import argo_memory
import argo_observe as observe
import argo_paths
import argo_store
import fetch_signals
import send_telegram
from argo_log import get_logger
from argo_webhook import _clean_reply

log = get_logger(__name__)

# Re-exported from argo_paths so this stays the module-level name tests patch
# (mock.patch.object(argo_watch, "SEEN_PATH", tmp)); load_seen/save_seen read it
# by bare name at call time, so the override still bites.
SEEN_PATH = argo_paths.SEEN_PATH
PER_FEED = 10          # consider this many recent items per feed
# Strength, not count, decides how many alerts fire: the judge keeps every item
# that genuinely clears the "a frontier builder must know this today" bar and
# nothing else. This is only a safety backstop so a pathological feed day can't
# blow up the phone — it is NOT a target and is not shown to the judge.
ALERT_SAFETY_CAP = 8
SEEN_CAP = 2000        # keep the seen-store bounded
MAX_ATTEMPTS = 3       # re-judge an un-alerted item this many times before retiring


_TITLE_TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")  # a leading "[news]"-style tag


def _canonical_url(link):
    """host + path, scheme- and query-insensitive, so the SAME article reaching
    us via two feeds (http vs https, a trailing slash, utm/ref tracking params)
    collapses to one id instead of alerting twice."""
    parts = urlsplit(link)
    if not parts.netloc:
        # Not a scheme://host URL (e.g. an Atom urn:/tag: id). Keep the raw
        # string so it still dedups, just without canonicalization.
        return link.lower()
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _normalize_title(title):
    """Lowercased, tag- and punctuation-stripped title, used only when an item has
    no link, so a re-punctuated or [tag]-prefixed reprint still matches."""
    t = _TITLE_TAG_RE.sub("", title)        # drop a leading [tag]
    t = re.sub(r"[^\w\s]", " ", t)          # punctuation -> space (don't glue words)
    return re.sub(r"\s+", " ", t).strip().lower()


def _item_id(item):
    """Stable identity for dedup: canonicalized link, else normalized title."""
    link = (item.get("link") or "").strip()
    if link:
        return _canonical_url(link)
    return _normalize_title(item.get("title") or "")


def load_seen():
    """Return the seen-store as {id: attempts}.

    Backward-compatible: the old format was a flat list of ids. We read those as
    already-settled (attempts == MAX_ATTEMPTS) so they're never re-judged.
    """
    data = argo_store.load_json(SEEN_PATH, {})
    if isinstance(data, list):
        return {i: MAX_ATTEMPTS for i in data}
    return data


def save_seen(seen):
    """Persist the seen-store, bounded to the most recent SEEN_CAP entries.

    dict preserves insertion order, so slicing the items keeps the newest.
    """
    bounded = dict(list(seen.items())[-SEEN_CAP:])
    argo_store.save_json(SEEN_PATH, bounded)


def collect_new(seen):
    """Fetch all feeds, return items still eligible for judging.

    An item is eligible if it's never been seen, or has been seen but not yet
    settled (fewer than MAX_ATTEMPTS un-alerted judgings). Settled means either
    alerted (recorded at MAX_ATTEMPTS) or judged-and-skipped MAX_ATTEMPTS times.
    """
    new = []
    for label, url in fetch_signals.FEEDS:
        for item in fetch_signals.fetch_feed(label, url)[:PER_FEED]:
            iid = _item_id(item)
            if iid and seen.get(iid, 0) < MAX_ATTEMPTS:
                new.append(item)
    return new


JUDGE_INSTRUCTIONS = """You are Argo, a frontier scout. Below are NEW items that
just appeared on AI frontier feeds. Pick ONLY the ones a frontier builder would
genuinely want to know about TODAY: real launches, new models, new tools/products,
notable capability jumps, or major lab announcements. DROP routine papers,
incremental research, listicles, and noise. Most items should be dropped.

NEVER miss a flagship launch from a major lab (OpenAI, Anthropic, Google
DeepMind, Meta, xAI/Grok, Mistral, DeepSeek) or a tool builders will adopt
widely. If a major lab ships a new model or product, that always clears the bar.

Keep EVERY item that genuinely clears this bar, and NO others. There is no target
number: most days that is 0 to 2, a big launch day might be 5 or more. Do NOT pad
to hit a count, and do NOT drop a must-know item just to stay short. Strength is
the only filter.

For each item you keep, write ONE short plain-text line (no markdown) saying what
it is and why it matters, then the link on its own.
If nothing clears the bar, output exactly: NONE

Format per kept item:
<one sharp sentence>
<link>

Items:
{items}
"""


def judge(new_items):
    """LLM judge -> list of alert lines, one per item that cleared the strength
    bar (no fixed count). Trimmed to ALERT_SAFETY_CAP only as a runaway backstop.
    [] if nothing qualifies."""
    if not new_items:
        return []

    listing = "\n\n".join(
        f"- {it['title']}\n  {it.get('summary','')[:200]}\n  {it.get('link','')}"
        for it in new_items[:60]  # cap prompt size
    )
    prompt = JUDGE_INSTRUCTIONS.format(items=listing)

    # Use the configured chat model (Claude if available, else gpt-4o fallback).
    # `or` not `.get(k, default)`: an empty ARGO_CHAT_MODEL (a set-but-unset CI var)
    # would otherwise win as "" and defeat the Sonnet default, routing to ARGO_MODEL.
    model = next(
        (m for m in [(os.environ.get("ARGO_CHAT_MODEL") or "claude-sonnet-4-6")]
         + observe.resolve_models()
         if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])),
        None,
    )
    if model is None:
        log.warning("no API key available for the judge -- skipping run")
        return []

    # temperature=0: a deterministic verdict so the same items don't flip between
    # runs (the bug that re-sent already-delivered drops). argo_observe omits the
    # param for models that reject a custom temperature (gpt-5/o-series, opus-4-8).
    try:
        if observe.provider_for(model)["name"] == "anthropic":
            raw = observe.chat_with_mcp(
                "You are Argo, a terse frontier scout.",
                [{"role": "user", "content": prompt}], model, temperature=0,
            )
        else:
            raw = observe.generate_observations(prompt, model, temperature=0)
    except Exception:
        # Log the judge failure from the watcher's own logger (the scheduler's outer
        # net also logs it) and re-raise: main() aborts before the seen-store update,
        # so items aren't penalized (attempt-bumped) for an infrastructure failure.
        log.error("watch judge failed on model %s", model, exc_info=True)
        raise

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
    return alerts[:ALERT_SAFETY_CAP]


def _was_alerted(item, alerts):
    """True if this item's link/title appears in any alert line we're sending."""
    link = (item.get("link") or "").strip().lower()
    title = (item.get("title") or "").strip().lower()
    blob = " ".join(alerts).lower()
    return bool((link and link in blob) or (title and title in blob))


def main():
    no_send = "--no-send" in sys.argv

    seen = load_seen()

    new_items = collect_new(seen)
    print(f"\n📡 Argo Watch — {len(new_items)} items eligible for judging")

    alerts = judge(new_items)
    log.info("watch run: %d eligible, %d cleared the bar", len(new_items), len(alerts))

    if not alerts:
        print("Nothing cleared the frontier-builder bar this run.")
    else:
        print(f"\n{len(alerts)} alert(s):")
        for a in alerts:
            print(f"  • {a}")
            if not no_send:
                msg = f"🛰️ Argo spotted something:\n\n{a}"
                send_telegram.send_message(msg)
                # Record the push into chat memory so a follow-up about this alert
                # ("is this a counter to X?") sees it -- proactive sends used to
                # bypass the log and Argo looked amnesiac. Best-effort: a memory
                # write must never block delivery.
                try:
                    argo_memory.record(os.environ.get("TELEGRAM_CHAT_ID"), "Argo", msg)
                except Exception:
                    log.warning("could not record watch alert to chat memory",
                                exc_info=True)

    if no_send:
        print("\n(--no-send: nothing sent, seen-store NOT updated)")
        print("\n✅ Watch complete.\n")
        return

    # Update the seen-store. An ALERTED item is settled (recorded at
    # MAX_ATTEMPTS so it's never reconsidered). An un-alerted item gets its
    # attempt count bumped, so a real drop the non-deterministic judge skipped
    # gets a few more shots before retiring -- instead of being lost forever.
    alerted = skipped = 0
    for it in new_items:
        iid = _item_id(it)
        if not iid:
            continue
        if _was_alerted(it, alerts):
            seen[iid] = MAX_ATTEMPTS
            alerted += 1
        else:
            seen[iid] = seen.get(iid, 0) + 1
            skipped += 1
    save_seen(seen)
    print(f"\nSeen-store: settled {alerted} alerted, bumped {skipped} un-alerted "
          f"({SEEN_PATH.relative_to(ROOT)}).")

    print("\n✅ Watch complete.\n")


if __name__ == "__main__":
    main()
