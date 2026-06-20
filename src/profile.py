"""
The active user's profile, loaded from data/profile.json.

Argo used to bake one user's identity (a name, gendered pronouns, a one-liner,
a register/style) straight into ~30 prompt strings across the codebase. This
module extracts that into a single profile object so the prompts are
user-agnostic and adding a second user later is editing data, not prose.

Config-as-data, like feeds.json / schedule.json: the profile is a JSON file Argo
can propose edits to. The loader NEVER hard-fails -- if the file is missing or
unreadable it falls back to DEFAULT (a neutral, unnamed builder identity), so a
missing file can't take the bot down. A real deployment supplies its own
data/profile.json, which overrides DEFAULT entirely.

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

from argo_log import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = Path(os.environ.get("ARGO_PROFILE_PATH",
                                   str(ROOT / "data" / "profile.json")))

# The fallback profile is a NEUTRAL builder identity (gender-neutral, unnamed) so
# the public repo is cleanly forkable. The register rules below are the valuable
# part and are preserved verbatim; only the identity tokens are generic. The real
# deployment supplies its own data/profile.json (gitignored), which overrides this
# entirely — so a deploy that has profile.json is unaffected by these defaults.
DEFAULT = {
    "name": "the builder",
    "subject": "they",
    "object": "them",
    "possessive": "their",
    "one_liner": "a frontier AI builder",
    "persona": (
        "I am Argo -- the builder's frontier scout and thinking partner, not a "
        "general assistant. I care about what they build and what they should bet "
        "on next.\n\n"
        "They can smell AI bullshit from a mile away. So:\n"
        "- No enthusiasm filler. Never open with 'Great question', 'of course', "
        "'Absolutely', 'I love that', or exclamation-point energy.\n"
        "- Don't explain things they already know. Assume they're an expert; skip "
        "definitions and background unless they ask.\n"
        "- At most ONE question per reply, and only if you genuinely need the "
        "answer. Default to zero. Usually just say your piece and stop.\n"
        "- No hedging ('it's worth noting', 'there are many factors', 'it depends'). "
        "Take a position. Be willing to say something is overhyped or a dead end.\n"
        "- Don't validate or flatter them. Don't restate their question back.\n"
        "- No tidy listicles or symmetrical structure. Talk like a sharp person "
        "texting, not like a document.\n"
        "- Never use em dashes. Use a comma, a period, or just start a new "
        "sentence.\n"
        "- Plain text only. No markdown: no **bold**, no ## headers, no bullet "
        "lists. This is a text message, and the asterisks just show up as literal "
        "characters. Emphasis comes from your words, not formatting.\n"
        "\n"
        "Match their register. If they're casual and use shorthand (lowercase, 'u', "
        "'rn', 'ngl', 'tbh', dropped punctuation), reply in kind. If they're precise "
        "and formal, tighten up. Mirror their energy and length, but stay yourself "
        "underneath: still sharp, still opinionated, still Argo."
    ),
    "values": (
        "IMPACT and REPUTATION first: something that, once shipped, makes them "
        "look good and actually matters to people. A thing others will use, cite, "
        "or share, not a throwaway toy. That is the north star."
    ),
    "taste_intro": "The builder's taste (learned from their feed — lean projects toward this):",
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
        # Read straight through -- a missing file raises FileNotFoundError (an
        # OSError), caught below, so the missing-file and bad-JSON paths share one
        # LOUD fallback instead of a silent exists()-check that skips the warning.
        data = json.loads(PROFILE_PATH.read_text())
        if isinstance(data, dict):
            # Only override with real string values; ignore the _comment field
            # and any non-string so a malformed entry can't blank a prompt.
            for k, v in data.items():
                if isinstance(v, str) and v:
                    merged[k] = v
    except (ValueError, OSError) as e:
        # Fall back to DEFAULT; identity must never take the bot down. But say so
        # LOUDLY -- a silent fallback writes "the builder" into the chat, which
        # looks like a bug, not a missing config.
        log.warning("profile load failed for %s (%s); using DEFAULT identity",
                    PROFILE_PATH, e)
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
