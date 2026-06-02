"""
Fetch fresh frontier signals from the internet (RSS/Atom) into data/signals.json.

The problem this solves: with a frozen signals.json, Argo only ever reframes the
same handful of signals, so every suggested project circles one theme. This
script refreshes the *input* — pulling recent items from curated frontier feeds
— so Observation -> Insight -> Bet runs over genuinely new material each time.

Behaviour:
  - pull recent items from FEEDS (parsed with feedparser if installed, else a
    stdlib-only fallback),
  - keep the most recent items, rotate the selection so consecutive runs differ,
  - normalize into the existing signal schema (title/source/category/summary +
    zeroed scores), and write data/signals.json.

Standalone. Does not call an LLM, does not touch Argo V1 (argo.py) or F-001.
Run with:  python src/fetch_signals.py
"""

import json
import random
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = ROOT / "data" / "signals.json"

# How many feed items to turn into signals for one run.
NUM_SIGNALS = 6
# Pull this many recent items per feed before selecting across feeds.
PER_FEED = 8
# How many of the most-recent items to sample from (rotation breaks the
# single-theme rut: each run picks a different subset of the fresh pool).
RECENT_POOL = 18

# Curated frontier feeds. Add/remove sources here — this is the control surface
# for what Argo "watches". Each: (label/category, url).
FEEDS = [
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("Hacker News (front)", "https://hnrss.org/frontpage"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
]

USER_AGENT = "argo-fetch-signals/1.0 (+https://github.com/yiyaw-lab/seas)"


def _fetch_url(url, timeout=20):
    """Fetch raw bytes for a feed URL, verifying TLS via certifi when present."""
    try:
        import certifi
        import ssl

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = None

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _parse_with_feedparser(raw):
    import feedparser

    parsed = feedparser.parse(raw)
    items = []
    for e in parsed.entries:
        items.append({
            "title": (e.get("title") or "").strip(),
            "summary": _clean(e.get("summary") or e.get("description") or ""),
            "published": e.get("published_parsed") or e.get("updated_parsed"),
        })
    return items


def _parse_with_stdlib(raw):
    """Minimal RSS/Atom fallback when feedparser isn't installed."""
    import xml.etree.ElementTree as ET

    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    items = []
    # RSS <item> and Atom <entry>; ignore namespaces by matching local names.
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = summary = ""
        for child in el:
            ctag = child.tag.split("}")[-1]
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag in ("summary", "description") and child.text:
                summary = _clean(child.text)
        if title:
            items.append({"title": title, "summary": summary, "published": None})
    return items


def _clean(text):
    """Strip tags/whitespace and cap length for a tidy signal summary."""
    import re

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def fetch_feed(label, url):
    try:
        raw = _fetch_url(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"  ! {label}: fetch failed ({exc})")
        return []

    try:
        import feedparser  # noqa: F401
        items = _parse_with_feedparser(raw)
    except ImportError:
        items = _parse_with_stdlib(raw)

    # Newest first when we have dates; keep a slice per feed.
    items = [i for i in items if i["title"]]
    items.sort(key=lambda i: i["published"] or (), reverse=True)
    for i in items:
        i["source"] = label
    return items[:PER_FEED]


def to_signal(item):
    return {
        "title": item["title"],
        "source": item["source"],
        "category": item["source"],
        "summary": item["summary"],
        "possible_capability_unlocked": "",
        "scores": {
            "durability": 0,
            "leverage": 0,
            "alignment": 0,
            "accessibility": 0,
            "novelty": 0,
        },
    }


def main():
    print("\n📡 Argo — Fetch Signals\n")

    pool = []
    for label, url in FEEDS:
        items = fetch_feed(label, url)
        print(f"  • {label}: {len(items)} items")
        pool.extend(items)

    if not pool:
        print("\n❌ No items fetched from any feed — leaving signals.json "
              "unchanged.")
        sys.exit(1)

    # De-dup by title, take the freshest RECENT_POOL, then randomly sample
    # NUM_SIGNALS so each run sees a different cross-section (breaks the
    # single-theme rut).
    seen = set()
    deduped = []
    for item in pool:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    recent = deduped[:RECENT_POOL]
    chosen = random.sample(recent, min(NUM_SIGNALS, len(recent)))
    signals = [to_signal(i) for i in chosen]

    SIGNALS_PATH.write_text(json.dumps(signals, indent=2) + "\n")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\nSelected {len(signals)} fresh signals (of {len(deduped)} unique):")
    for s in signals:
        print(f"  - [{s['source']}] {s['title'][:70]}")
    print(f"\nWrote {SIGNALS_PATH.relative_to(ROOT)} @ {stamp}")
    print("\n✅ Signals refreshed.\n")


if __name__ == "__main__":
    main()
