"""
Argo V2 — Phase G: the tripwire (proactive frontier alerts).

Flips Argo from reactive to proactive. On a schedule (Railway local_loop, with
direct CLI runs still supported) this watcher:
  1. fetches current items from the frontier feeds (fetch_signals.FEEDS), plus
     optional live X/web items via Grok (grok_search) when ARGO_GROK_SOURCE=1,
  2. dedups against a seen-store (data/argo_seen.json) so only genuinely NEW
     items are considered,
  3. runs an LLM judge: "would a frontier builder want to know this today?",
     keeping the strongest eligible items from the never-before-seen pool,
  4. texts Yiya the items that clear that bar, topping up to a small quiet-day
     floor from fresh items only; ALERT_SAFETY_CAP bounds a runaway run,
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
from urllib.parse import parse_qsl, urlencode, urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import argo_memory
import argo_observe as observe
import argo_paths
import argo_pushes
import argo_store
import argo_watch_runs
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
# Keep a small quiet-day floor from genuinely fresh items, while still bounding a
# pathological feed day. The floor is enforced after the LLM verdict so a model
# that says NONE cannot starve the tripwire; items already in the seen-store are
# never used to satisfy the floor.
ALERT_SAFETY_CAP = 8
ALERT_FLOOR = 2
SEEN_CAP = 5000        # keep the seen-store bounded (see LRU touch in collect_new)
MAX_ATTEMPTS = 3       # re-judge an un-alerted item this many times before retiring


_TITLE_TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")  # a leading "[news]"-style tag


# Query params that are pure tracking/marketing noise -- dropped so the SAME
# article reaching us via two tracking URLs dedups. Every OTHER param is treated
# as identity-bearing and KEPT: dropping the whole query collapsed every Hacker
# News item (/item?id=N) and YouTube video (/watch?v=N) to one id, silently
# killing those feeds after their first item ever seen.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "ncid",
})


def _canonical_url(link):
    """host + path + identity query, scheme-insensitive, with tracking params
    stripped, so the SAME article reaching us via two feeds (http vs https, a
    trailing slash, utm/ref tracking) collapses to one id -- without collapsing
    distinct items that live under a shared path (HN /item, YouTube /watch)."""
    parts = urlsplit(link)
    if not parts.netloc:
        # Not a scheme://host URL (e.g. an Atom urn:/tag: id). Keep the raw
        # string so it still dedups, just without canonicalization.
        return link.lower()
    kept = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if k.lower() not in _TRACKING_PARAMS)
    base = f"{parts.netloc.lower()}{parts.path.rstrip('/')}"
    return f"{base}?{urlencode(kept)}" if kept else base


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


def _touch_and_eligible(seen, iid):
    """LRU-touch a fetched id and report whether it's still eligible for judging.

    Eligible = never seen, or seen but not yet settled (< MAX_ATTEMPTS un-alerted
    judgings). Side effect: an id already in `seen` is moved to the store's newest
    end. save_seen evicts oldest-first; without the touch, a settled item that
    KEEPS circulating in a feed (GitHub Trending resurfaces repos for weeks) kept
    its original slot, fell off the SEEN_CAP edge while still live, and re-alerted
    -- the news-repeats-across-weeks bug. Shared by both collectors so the touch
    semantics can't drift between them."""
    if iid in seen:
        seen[iid] = seen.pop(iid)
    return seen.get(iid, 0) < MAX_ATTEMPTS


def collect_new(seen):
    """Fetch all feeds, return items still eligible for judging (see
    _touch_and_eligible for the eligibility rule and the LRU-touch side effect on
    `seen`)."""
    new = []
    for label, url in fetch_signals.FEEDS:
        for item in fetch_signals.fetch_feed(label, url)[:PER_FEED]:
            iid = _item_id(item)
            if iid and _touch_and_eligible(seen, iid):
                new.append(item)
    return new


def collect_grok(seen, already):
    """Optional live X/web frontier candidates via Grok (xAI Agent Tools), additive
    to the RSS pool. Gated on ARGO_GROK_SOURCE=1 + XAI_API_KEY (a paid call). Dedups
    against the seen-store AND the items already collected this run, so the same
    story from RSS + Grok isn't double-judged. Failures degrade to [] inside
    grok_search, so this never breaks the RSS tripwire."""
    try:
        import grok_search
    except ImportError:
        return []
    if not grok_search.is_enabled():
        log.info("grok source off (ARGO_GROK_SOURCE=%s, key_present=%s); RSS only",
                 os.environ.get("ARGO_GROK_SOURCE"),
                 bool((os.environ.get("XAI_API_KEY") or "").strip()))
        return []
    have = {_item_id(it) for it in already}
    out = []
    for item in grok_search.fetch():
        iid = _item_id(item)
        if not iid or iid in have:
            continue
        if _touch_and_eligible(seen, iid):
            have.add(iid)
            out.append(item)
    log.info("grok source added %d new candidate(s)", len(out))
    return out


JUDGE_INSTRUCTIONS = """You are Argo, a frontier scout. Below are NEW items that
just appeared on AI frontier feeds. Pick ONLY the ones a frontier builder would
genuinely want to know about TODAY: real launches, new models, new tools/products,
notable capability jumps, or major lab announcements. DROP routine papers,
incremental research, listicles, and noise. Most items should be dropped.

NEVER miss a flagship launch from a major lab (OpenAI, Anthropic, Google
DeepMind, Meta, xAI/Grok, Mistral, DeepSeek) or a tool builders will adopt
widely. If a major lab ships a new model or product, that always clears the bar.

These items have already been filtered against the seen-store. Keep EVERY item
that genuinely clears this bar, and on quiet days still keep the best 1 or 2
fresh candidates so the scout keeps learning what is worth noticing. Do NOT drop
a must-know item just to stay short.

For each item you keep, write ONE short plain-text line (no markdown) saying what
it is and why it matters, then the link on its own.
Use NONE only if every item is clearly irrelevant, spam, broken, or non-AI.

Format per kept item:
<one sharp sentence>
<link>

Items:
{items}
"""


def judge(new_items):
    """LLM judge -> alert lines for must-know items plus quiet-day candidates.

    Trimmed to ALERT_SAFETY_CAP only as a runaway backstop. [] means the judge
    found only irrelevant, spam, broken, or non-AI input; main() may still top up
    from never-before-seen items if the model under-keeps.
    """
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


def _fallback_alert(item):
    """Plain-text alert for a fresh item the LLM judge did not keep."""
    title = re.sub(r"\s+", " ", (item.get("title") or "").strip())
    if not title:
        title = "Fresh frontier-feed item"
    summary = re.sub(r"\s+", " ", (item.get("summary") or "").strip())
    summary = re.sub(r"<[^>]+>", "", summary).strip()
    if summary:
        sentence = f"{title}: {summary[:180].rstrip()}"
    else:
        sentence = f"{title}: fresh frontier-feed item worth a quick look."
    link = (item.get("link") or "").strip()
    return f"{sentence}\n{link}" if link else sentence


def _alert_mentions_seen_item(alert, new_items, seen_before):
    """Best-effort check that an alert points at an already-seen item."""
    for item in new_items:
        iid = _item_id(item)
        if iid in seen_before and _was_alerted(item, [alert]):
            return True
    return False


def _ensure_alert_floor(new_items, alerts, seen_before):
    """Top up alerts to ALERT_FLOOR using only never-before-seen items."""
    floor = min(ALERT_FLOOR, ALERT_SAFETY_CAP)
    out = [
        alert for alert in alerts[:ALERT_SAFETY_CAP]
        if not _alert_mentions_seen_item(alert, new_items, seen_before)
    ]
    if len(out) >= floor:
        return out[:ALERT_SAFETY_CAP]

    for item in new_items:
        iid = _item_id(item)
        if not iid or iid in seen_before or _was_alerted(item, out):
            continue
        out.append(_fallback_alert(item))
        if len(out) >= floor or len(out) >= ALERT_SAFETY_CAP:
            break
    return out[:ALERT_SAFETY_CAP]


def main():
    no_send = "--no-send" in sys.argv
    run = argo_watch_runs.new_run(no_send=no_send)

    try:
        seen = load_seen()
        seen_before = set(seen)

        rss_items = collect_new(seen)
        grok_items = collect_grok(seen, rss_items)  # optional live X/web source
        new_items = rss_items + grok_items
        run["rss_candidates"] = len(rss_items)
        run["grok_candidates"] = len(grok_items)
        run["candidates"] = len(new_items)
        print(f"\n📡 Argo Watch — {len(new_items)} items eligible for judging")

        judged_alerts = judge(new_items)
        alerts = _ensure_alert_floor(new_items, judged_alerts, seen_before)
        if len(alerts) > len(judged_alerts):
            log.info("watch floor kept %d fresh fallback candidate(s)",
                     len(alerts) - len(judged_alerts))
        run["judge_kept"] = len(alerts)
        log.info("watch run: %d eligible, %d cleared the bar",
                 len(new_items), len(alerts))

        if not alerts:
            print("Nothing cleared the frontier-builder bar this run.")
        else:
            print(f"\n{len(alerts)} alert(s):")
            for a in alerts:
                print(f"  • {a}")
                if not no_send:
                    msg = f"🛰️ Argo spotted something:\n\n{a}"
                    # Instrument the push so act_on_rate can tell whether it landed
                    # (a user reply links it in the webhook). The /push round-trip
                    # also runs the F6 steerable-proactiveness gate on the volume and
                    # bridges the verdict back: when result.suppressed the alert is
                    # below the bar, so skip both the send and the chat-memory record.
                    # A POST failure is fail-open (not suppressed), so a flaky webhook
                    # never silences an alert.
                    suppressed = False
                    try:
                        result = argo_pushes.post_to_webhook("watch", msg)
                        suppressed = result.suppressed
                    except Exception as exc:
                        argo_watch_runs.add_error(
                            run, f"push instrumentation failed: {type(exc).__name__}: {exc}")
                        log.warning("could not instrument watch push", exc_info=True)
                    if suppressed:
                        run["suppressed"] += 1
                        argo_watch_runs.add_suppression(run, "proactiveness gate")
                        log.info("F6 gate: watch alert below threshold, suppressed")
                        continue
                    try:
                        send_telegram.send_message(msg)
                    except SystemExit as exc:
                        argo_watch_runs.add_error(run, f"telegram send failed: {exc}")
                        raise
                    run["sent"] += 1
                    # Record the push into chat memory so a follow-up about this alert
                    # ("is this a counter to X?") sees it. Best-effort: a memory
                    # write must never block delivery.
                    try:
                        argo_memory.record(os.environ.get("TELEGRAM_CHAT_ID"),
                                           "Argo", msg)
                    except Exception as exc:
                        argo_watch_runs.add_error(
                            run, f"chat-memory record failed: {type(exc).__name__}: {exc}")
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
        run["seen_store_written"] = True
        # SEEN_PATH sits under ROOT only in the ephemeral-checkout case (bad: data is
        # lost on redeploy); on the volume (SEEN_PATH=/data/..., ROOT=/app) it never
        # is, and that's the CORRECT placement -- relative_to would raise ValueError
        # there, which used to crash this print (and the scheduler's `watch` entry)
        # on every run. is_relative_to never raises, so show the relative path in the
        # bad case and the plain absolute path (no warning) in the good one.
        shown = SEEN_PATH.relative_to(ROOT) if SEEN_PATH.is_relative_to(ROOT) else SEEN_PATH
        print(f"\nSeen-store: settled {alerted} alerted, bumped {skipped} un-alerted "
              f"({shown}).")

        print("\n✅ Watch complete.\n")
    except Exception as exc:
        argo_watch_runs.add_error(run, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        argo_watch_runs.finish(run)


if __name__ == "__main__":
    main()
