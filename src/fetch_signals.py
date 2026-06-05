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

# Curated frontier feeds — the control surface for what Argo "watches".
# Balanced across three lenses so insights don't collapse to one theme:
#   research (arXiv), what builders ship/star (GitHub), and company releases.
# Feeds live in data/feeds.json (DATA, so Argo can propose new ones via a small
# Contents-only PR instead of rewriting this file). The hardcoded list below is a
# fallback if the JSON is missing/unreadable, so signal fetching never breaks.
# Each FEEDS entry stays (label, url) for all existing consumers.
_FEEDS_FALLBACK = [
    ("arXiv cs.AI", "https://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "https://export.arxiv.org/rss/cs.LG"),
    ("arXiv cs.CL", "https://export.arxiv.org/rss/cs.CL"),
    ("GitHub Trending", "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"),
    ("GitHub Trending (Python)", "https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml"),
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("GitHub Changelog", "https://github.blog/changelog/feed/"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
]


def _load_feeds():
    path = ROOT / "data" / "feeds.json"
    try:
        data = json.loads(path.read_text())
        feeds = [(f["label"], f["url"]) for f in data["feeds"]
                 if f.get("label") and f.get("url")]
        if feeds:
            return feeds
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        pass
    return _FEEDS_FALLBACK


FEEDS = _load_feeds()

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
            "link": (e.get("link") or e.get("id") or "").strip(),
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
        title = summary = link = ""
        for child in el:
            ctag = child.tag.split("}")[-1]
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag in ("summary", "description") and child.text:
                summary = _clean(child.text)
            elif ctag == "link":
                # RSS: link text; Atom: href attribute.
                link = (child.text or child.get("href") or "").strip()
            elif ctag in ("id", "guid") and child.text and not link:
                link = child.text.strip()
        if title:
            items.append({"title": title, "summary": summary,
                          "published": None, "link": link})
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


def lens_of(source):
    """Map a feed's source label to one of three lenses for balanced selection."""
    s = source.lower()
    if s.startswith("arxiv"):
        return "research"
    if "github trending" in s:
        return "github"
    return "company"  # OpenAI / HF / GitHub Changelog / Google AI


def to_signal(item):
    return {
        "title": item["title"],
        "source": item["source"],
        "category": item["source"],
        "summary": item["summary"],
        # Keep the source link: the V3 SEAS synthesis floor (seas_finding.py)
        # fetches the real page behind a signal to ground a finding in cited
        # evidence. Without this, a signal has no source to pull. Existing
        # consumers ignore the extra field, so this is backward-compatible.
        "link": item.get("link", ""),
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

    # De-dup by title.
    seen = set()
    deduped = []
    for item in pool:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    # Stratify by lens so the high-volume academic feed can't dominate the pick.
    # Round-robin across lenses, sampling within each, until we have NUM_SIGNALS.
    # This is what keeps the selection balanced (fetching balanced isn't enough).
    buckets = {"research": [], "github": [], "company": []}
    for item in deduped:
        buckets[lens_of(item["source"])].append(item)

    for items in buckets.values():
        random.shuffle(items)

    chosen = []
    order = ["research", "github", "company"]
    while len(chosen) < NUM_SIGNALS and any(buckets[l] for l in order):
        for lens in order:
            if buckets[lens] and len(chosen) < NUM_SIGNALS:
                chosen.append(buckets[lens].pop())

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
