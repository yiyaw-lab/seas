"""
The active user's profile, loaded from data/profile.json.

Argo used to bake one user's identity ("Yiya", "she", "a frontier AI builder",
her register/style) straight into ~30 prompt strings across the codebase. This
module extracts that into a single profile object so the prompts are
user-agnostic and adding a second user later is editing data, not prose.

Config-as-data, like feeds.json / schedule.json: the profile is a JSON file Argo
can propose edits to. The loader NEVER hard-fails -- if the file is missing or
unreadable it falls back to DEFAULT (the original Yiya values), so output is
identical to before the extraction and a missing file can't take the bot down.

data/profile.json itself is gitignored (in a multi-user app, a real user's
identity should never be committed). data/profile.example.json IS committed as the
schema template -- copy it to data/profile.json and edit, the way you'd copy
.env.example to .env.

Scope: ONE active user. The data stores (projects/taste/chat) stay global for
now; this is only the identity/persona/voice. Path is overridable via
ARGO_PROFILE_PATH (mirrors ARGO_CHAT_LOG) so tests can point at a temp profile.

Stdlib only (no new deps).
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = Path(os.environ.get("ARGO_PROFILE_PATH",
                                   str(ROOT / "data" / "profile.json")))

# The fallback profile IS the pre-extraction Yiya identity, so behavior is
# unchanged if data/profile.json is absent. Keep these byte-identical to the
# strings they replaced in the prompts.
DEFAULT = {
    "name": "Yiya",
    "subject": "she",
    "object": "her",
    "possessive": "her",
    "one_liner": "a frontier AI builder",
    "persona": (
        "I am Argo -- Yiya's frontier scout and thinking partner, not a general "
        "assistant. I care about what Yiya builds and what she should bet on next.\n\n"
        "She can smell AI bullshit from a mile away. So:\n"
        "- No enthusiasm filler. Never open with 'Great question', 'of course', "
        "'Absolutely', 'I love that', or exclamation-point energy.\n"
        "- Don't explain things she already knows. Assume she's an expert; skip "
        "definitions and background unless she asks.\n"
        "- At most ONE question per reply, and only if you genuinely need the "
        "answer. Default to zero. Usually just say your piece and stop.\n"
        "- No hedging ('it's worth noting', 'there are many factors', 'it depends'). "
        "Take a position. Be willing to say something is overhyped or a dead end.\n"
        "- Don't validate or flatter her. Don't restate her question back to her.\n"
        "- No tidy listicles or symmetrical structure. Talk like a sharp person "
        "texting, not like a document.\n"
        "- Never use em dashes. Use a comma, a period, or just start a new "
        "sentence.\n"
        "- Plain text only. No markdown: no **bold**, no ## headers, no bullet "
        "lists. This is a text message, and the asterisks just show up as literal "
        "characters. Emphasis comes from your words, not formatting.\n"
        "\n"
        "Match her register. If she's casual and uses shorthand (lowercase, 'u', "
        "'rn', 'ngl', 'tbh', dropped punctuation), reply in kind. If she's precise "
        "and formal, tighten up. Mirror her energy and length, but stay yourself "
        "underneath: still sharp, still opinionated, still Argo."
    ),
    "values": (
        "IMPACT and REPUTATION first: something that, once shipped, makes her "
        "look good and actually matters to people. A thing others will use, cite, "
        "or share, not a throwaway toy. That is the north star."
    ),
    "taste_intro": "Yiya's taste (learned from her feed — lean projects toward this):",
}

_cache = None


def load():
    """Return the active profile dict. Reads data/profile.json once and caches it;
    on any problem (missing file, bad JSON) falls back to DEFAULT so callers never
    crash. Unknown keys in the file are kept; missing keys fall back per-key to
    DEFAULT, so a partial profile still produces a complete one."""
    global _cache
    if _cache is not None:
        return _cache
    merged = dict(DEFAULT)
    try:
        if PROFILE_PATH.exists():
            data = json.loads(PROFILE_PATH.read_text())
            if isinstance(data, dict):
                # Only override with real string values; ignore the _comment field
                # and any non-string so a malformed entry can't blank a prompt.
                for k, v in data.items():
                    if isinstance(v, str) and v:
                        merged[k] = v
    except (ValueError, OSError):
        pass  # fall back to DEFAULT; identity must never take the bot down
    _cache = merged
    return _cache


def reload():
    """Drop the cache so the next load() re-reads the file. For tests that swap
    ARGO_PROFILE_PATH between profiles."""
    global _cache
    _cache = None


# --- convenience accessors used at the prompt-building call sites -------------

def name():
    return load()["name"]


def one_liner():
    return load()["one_liner"]


def persona():
    return load()["persona"]


def values():
    return load()["values"]


def taste_intro():
    return load()["taste_intro"]


def pronoun(kind="subject"):
    """kind: 'subject' (she/he/they), 'object' (her/him/them), or 'possessive'
    (her/his/their). Falls back to the subject form for an unknown kind."""
    p = load()
    return p.get(kind) or p["subject"]
