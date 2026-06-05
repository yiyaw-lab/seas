"""
Taste signals — what Yiya likes, learned from her feed, to shape what Argo builds.

When she texts Argo a screenshot of an app/design/product she likes, the lesson
shouldn't evaporate after one reply. This store captures the DURABLE lesson — the
pattern she responded to and why — so it can fold into project generation and
measurably pull future bets toward her taste. A screenshot becomes evidence about
taste, the same way a finding becomes a belief.

Deliberately lightweight: a screenshot is a soft preference signal, not a
falsifiable claim, so it does NOT go through the finding emission gate or the
world model (those are for claims about what's TRUE; taste is about what she
LIKES). Separate store, separate purpose.

Standard-library only. JSON store at data/taste_signals.json.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASTE_PATH = ROOT / "data" / "taste_signals.json"

# How many recent taste signals to fold into a generation prompt.
RECENT_FOR_PROMPT = 8


def _load():
    if not TASTE_PATH.exists():
        return []
    try:
        return json.loads(TASTE_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _save(items):
    TASTE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASTE_PATH.write_text(json.dumps(items, indent=2) + "\n")


# The extraction prompt: turn a screenshot into a structured taste signal. Asks
# the vision model to name the PATTERN she likely responded to, not just describe
# pixels — the transferable lesson is the point.
EXTRACT_SYSTEM = (
    "You study what a sharp frontier builder likes, from screenshots she sends. "
    "You extract the transferable LESSON — the product/design/interaction pattern "
    "worth stealing — not a pixel-by-pixel description."
)

EXTRACT_PROMPT = """This is a screenshot the user sent because she liked
something about it{caption_clause}. Look at it and return ONLY a JSON object:

{{
  "what": "<the app/screen/thing, one phrase>",
  "pattern": "<the transferable design/product/interaction pattern she likely responded to>",
  "liked": "<the underlying quality that makes it good (e.g. low-friction capture, calm density, fast feedback)>",
  "steal": "<how this could inform something SHE might build — concrete>",
  "caption_echo": "<if she gave a caption, the intent you read from it; else empty>"
}}

Be specific and concrete. The 'pattern' and 'liked' fields are the durable
lesson; vague answers are useless. No prose outside the JSON."""


def build_extract_prompt(caption=""):
    clause = f' (her caption: "{caption}")' if caption else ""
    return EXTRACT_PROMPT.format(caption_clause=clause)


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


def format_for_prompt(limit=RECENT_FOR_PROMPT):
    """Compact recent taste signals for folding into project generation. Empty
    string if none, so the caller can skip the section cleanly."""
    items = _load()[-limit:]
    if not items:
        return ""
    lines = ["Yiya's recent taste signals (things she liked, from her feed):"]
    for s in items:
        line = f"- {s['pattern']}"
        if s.get("liked"):
            line += f" (why it works: {s['liked']})"
        lines.append(line)
    return "\n".join(lines)
