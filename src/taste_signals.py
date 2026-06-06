"""
Taste signals — what the user likes, learned from their feed, to shape what Argo
builds. (Whose taste: the active profile; see profile.py.)

When the user texts Argo a screenshot of an app/design/product they like, the
lesson shouldn't evaporate after one reply. This store captures the DURABLE
lesson — the pattern they responded to and why — so it can fold into project
generation and measurably pull future bets toward their taste. A screenshot
becomes evidence about taste, the same way a finding becomes a belief.

Deliberately lightweight: a screenshot is a soft preference signal, not a
falsifiable claim, so it does NOT go through the finding emission gate or the
world model (those are for claims about what's TRUE; taste is about what the user
LIKES). Separate store, separate purpose.

Standard-library only. JSON store at data/taste_signals.json.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import argo_store
import profile

ROOT = Path(__file__).resolve().parent.parent
TASTE_PATH = ROOT / "data" / "taste_signals.json"

# How many recent taste signals to fold into a generation prompt.
RECENT_FOR_PROMPT = 8


def _load():
    return argo_store.load_json(TASTE_PATH, [])


def _save(items):
    TASTE_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(TASTE_PATH, items)


# The extraction prompt: turn a screenshot into a structured taste signal. Asks
# the vision model to name the PATTERN the user likely responded to, not just
# describe pixels — the transferable lesson is the point. The user persona and
# pronouns come from the active profile (see profile.py).
def build_extract_system():
    subj = profile.pronoun("subject")
    return (
        f"You study what {profile.one_liner()} likes, from screenshots {subj} sends. "
        "You extract the transferable LESSON — the product/design/interaction pattern "
        "worth stealing — not a pixel-by-pixel description."
    )


EXTRACT_PROMPT = """This is a screenshot the user sent because {subj} liked
something about it{caption_clause}. Look at it and return ONLY a JSON object:

{{
  "what": "<the app/screen/thing, one phrase>",
  "pattern": "<the transferable design/product/interaction pattern {subj} likely responded to>",
  "liked": "<the underlying quality that makes it good (e.g. low-friction capture, calm density, fast feedback)>",
  "steal": "<how this could inform something {Subj} might build — concrete>",
  "caption_echo": "<if {subj} gave a caption, the intent you read from it; else empty>"
}}

Be specific and concrete. The 'pattern' and 'liked' fields are the durable
lesson; vague answers are useless. No prose outside the JSON."""


def build_extract_prompt(caption=""):
    subj = profile.pronoun("subject")
    poss = profile.pronoun("possessive")
    Subj = subj[:1].upper() + subj[1:]
    clause = f' ({poss} caption: "{caption}")' if caption else ""
    return EXTRACT_PROMPT.format(caption_clause=clause, subj=subj, Subj=Subj)


def parse_and_store(extraction_text, caption="", source="telegram-screenshot"):
    """Parse the vision model's JSON extraction and persist it as a taste signal.
    Returns (signal_dict, short_human_summary) or (None, None) if unparseable."""
    obj = _extract_json(extraction_text)
    if not obj or not obj.get("pattern"):
        return None, None
    items = _load()
    sig = {
        "id": f"T-{len(items) + 1:03d}",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": source,
        "caption": caption,
        "what": obj.get("what", ""),
        "pattern": obj.get("pattern", ""),
        "liked": obj.get("liked", ""),
        "steal": obj.get("steal", ""),
    }
    items.append(sig)
    _save(items)
    summary = f"{sig['pattern']}"
    if sig["liked"]:
        summary += f" — the win is {sig['liked']}"
    return sig, summary


def _extract_json(text):
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        s, e = text.find("{"), text.rfind("}")
        raw = text[s:e + 1] if (s != -1 and e > s) else None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def save_signal(what, pattern, liked, steal, source, caption=""):
    """Persist an already-structured taste signal (e.g. from a studied URL, where
    the model returns the lesson directly rather than via parse_and_store). Same
    record shape as the screenshot path. Returns the stored signal."""
    if not pattern:
        return None
    items = _load()
    sig = {
        "id": f"T-{len(items) + 1:03d}",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": source,
        "caption": caption,
        "what": what or "",
        "pattern": pattern,
        "liked": liked or "",
        "steal": steal or "",
    }
    items.append(sig)
    _save(items)
    return sig


# --- First-class learning: themes (so taste sharpens as it accumulates) ------
# A flat list of signals is memory; THEMES are learning. Recurring 'liked'
# qualities cluster into named taste-themes, so the profile gets sharper the more
# the user teaches it. Lightweight keyword clustering (stdlib) — taste is a soft
# preference, not a claim, so this stays heuristic, not a model call per read.

_STOP = {"the", "a", "an", "of", "to", "and", "in", "on", "for", "with", "is",
         "it", "that", "this", "without", "no", "low", "high", "more", "less"}


def _keywords(text):
    words = re.findall(r"[a-z][a-z-]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOP]


def themes(min_count=2):
    """Cluster taste signals by recurring keywords in their 'liked'/'pattern'
    fields. Returns [(theme_word, count, [signal_ids])] for words appearing in
    >= min_count signals, most common first. This is the emergent taste profile."""
    items = _load()
    from collections import defaultdict
    hits = defaultdict(list)
    for s in items:
        seen = set()
        for w in _keywords(s.get("liked", "")) + _keywords(s.get("pattern", "")):
            if w not in seen:
                hits[w].append(s["id"])
                seen.add(w)
    clustered = [(w, len(ids), ids) for w, ids in hits.items() if len(ids) >= min_count]
    clustered.sort(key=lambda x: x[1], reverse=True)
    return clustered


def format_for_prompt(limit=RECENT_FOR_PROMPT, p=None):
    """Compact recent taste signals + emergent themes for folding into project
    generation. Empty string if none, so the caller can skip the section. The
    intro line (whose taste this is) comes from the active profile; pass `p` to
    format for a specific user."""
    items = _load()
    if not items:
        return ""
    p = p or profile.load()
    lines = [p["taste_intro"]]
    th = themes()
    if th:
        lines.append("Recurring themes: "
                     + ", ".join(f"{w} (x{n})" for w, n, _ in th[:5]))
    lines.append("Recent signals:")
    for s in items[-limit:]:
        line = f"- {s['pattern']}"
        if s.get("liked"):
            line += f" (why it works: {s['liked']})"
        lines.append(line)
    return "\n".join(lines)


def format_profile():
    """Human-readable taste profile for read_taste (what the user + Argo inspect).
    Shows themes first (the learning), then the signals (the evidence)."""
    items = _load()
    if not items:
        return ("No taste signals yet. Send Argo a screenshot or a url of "
                "something you like and it'll start learning your taste.")
    out = [f"Taste profile — {len(items)} signal(s) learned from your feed.\n"]
    th = themes()
    if th:
        out.append("Themes you keep coming back to:")
        for w, n, ids in th[:8]:
            out.append(f"  {w} — {n}x ({', '.join(ids)})")
        out.append("")
    out.append("Signals:")
    for s in items:
        out.append(f"  {s['id']} [{s['source']}, {s['date']}]: {s['pattern']}"
                   + (f" — likes: {s['liked']}" if s.get("liked") else ""))
    return "\n".join(out)
