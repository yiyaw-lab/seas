"""Argo's chat memory -- the one append-only conversation log, shared.

This was private to argo_webhook (`_append_turn`/`_recent_turns`), so only turns
that came IN through the webhook were remembered. Argo's PROACTIVE sends -- the
weekly project (argo_project) and tripwire alerts (argo_watch) -- went straight to
Telegram via send_telegram and never landed here, so when the user replied about an
article Argo had just pushed ("is this a counter to Mythos?"), the webhook had no
record of it and Argo looked like it had amnesia.

Extracting the store into its own tiny module lets the proactive senders record
what they send WITHOUT importing the heavy Flask/MCP webhook (which would pull in
half the app and risk an import cycle). The webhook now delegates to this.

chat_id is normalized to str on both write and read: the webhook gets an int
chat_id from Telegram, but the proactive path keys off the TELEGRAM_CHAT_ID env
var (a string). Without normalization the webhook's reads wouldn't see the
proactive writes -- defeating the whole point. Comparing as str unifies them and
still matches legacy int-keyed entries.

Backed by the volume-capable ARGO_CHAT_LOG path (see argo_paths); stdlib + the
shared argo_store I/O only.
"""

import re
from datetime import datetime, timezone

import argo_paths
import argo_store

# Module-level so tests can patch it (mock.patch.object(argo_memory, "CHAT_LOG_PATH",
# tmp)); record/recent read this global at call time so the override bites.
CHAT_LOG_PATH = argo_paths.CHAT_LOG_PATH

# How many recent turns to feed the model as short-term memory.
HISTORY_TURNS = 12

# Common function words dropped before keyword overlap so "the/and/is" don't make
# unrelated turns look relevant. Deliberately small -- this is recall, not search.
_STOPWORDS = frozenset((
    "the and that have for not with you this but his from they say her she will one all "
    "would there their what out about who get which when make can like time just him know "
    "take person into year your good some could them than then now look only come its over "
    "think also back after use two how our work first well way even new want because any "
    "these give day most are was were has had did does done your you're i'm it's that's"
).split())


def record(chat_id, role, text):
    """Append one turn to the durable chat log (creates the file/dir if needed).

    No-op if chat_id is falsy (e.g. TELEGRAM_CHAT_ID unset on a proactive call)
    so we never write a turn that can't be keyed back to a conversation.
    """
    record_many(chat_id, [(role, text)])


def record_many(chat_id, turns):
    """Append multiple turns in a single read-write cycle. `turns` is a list of
    (role, text) pairs. Use instead of calling record() N times back-to-back to
    halve the I/O for the common user+Argo two-turn write per chat message."""
    if not chat_id or not turns:
        return
    CHAT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = argo_store.load_json(CHAT_LOG_PATH, [])
    if not isinstance(log, list):
        log = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for role, text in turns:
        log.append({
            "ts": ts,
            "chat_id": str(chat_id),
            "role": role,
            "text": text,
        })
    argo_store.save_json(CHAT_LOG_PATH, log)


def recent(chat_id, n=HISTORY_TURNS):
    """Return the last n turns for this chat (str-compared, so proactive sends and
    legacy int-keyed turns are both visible). Empty list if none/unreadable."""
    log = argo_store.load_json(CHAT_LOG_PATH, [])
    if not isinstance(log, list):
        return []
    turns = [t for t in log if str(t.get("chat_id")) == str(chat_id)]
    return turns[-n:]


def _content_tokens(text):
    """Lowercase word tokens, minus stopwords and 1-2 char noise, for overlap scoring."""
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def relevant(chat_id, query, k=3, exclude_recent=HISTORY_TURNS):
    """Recall up to k OLDER turns (those BEFORE the recent() window) whose words
    overlap `query`, so a fact from turn 3 survives past turn 15. Pure keyword +
    recency scoring -- no embeddings, no model call, no network -- which is plenty
    for a small per-chat log and swappable for embeddings later behind this same
    signature if a corpus ever outgrows a keyword scan.

    Only turns outside the recency window are searched: the last `exclude_recent`
    turns are already in the prompt, so re-surfacing them would just duplicate
    context. Ranked by overlap count, ties broken toward the more recent turn.
    Returns [] when the query is all-stopwords or nothing overlaps. Never raises."""
    try:
        q = _content_tokens(query)
        if not q:
            return []
        log = argo_store.load_json(CHAT_LOG_PATH, [])
        if not isinstance(log, list):
            return []
        turns = [t for t in log if str(t.get("chat_id")) == str(chat_id)]
        older = turns[:-exclude_recent] if exclude_recent else turns
        scored = []
        for i, t in enumerate(older):
            overlap = len(q & _content_tokens(t.get("text", "")))
            if overlap:
                scored.append((overlap, i, t))  # i as recency tiebreak (later = larger)
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [t for _, _, t in scored[:k]]
    except Exception:
        return []  # recall is best-effort; never break a chat turn over it
