"""Acted-on-push instrumentation -- did Argo's unprompted "push" messages land?

Argo sends scheduled/proactive messages (the weekly project from argo_project,
tripwire alerts from argo_watch) straight to Telegram. Until now there was no
measurement of whether any of them prompted a reply -- a push that nobody acts on
is noise we keep paying for. This thin store records one row per push and links
the next genuine user reply back to the most recent open push, so act_on_rate()
gives a single honest number: of the pushes we sent, what fraction got engaged.

The store is global (one push log, not per chat): per the PRD the live bot pushes
to a single owner, so link_reply accepts chat_id for signature parity/logging but
keys the link purely on recency + the time window. A reply links the most-recent
still-open push within LINK_WINDOW_SECONDS; later replies past the window, or with
no open push, link nothing.

Backed by the volume-capable ARGO_PUSHES_PATH (see argo_paths); stdlib + the
shared argo_store I/O and argo_log only.
"""

import hashlib
import time

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Module-level so tests can patch it (mock.patch.object(argo_pushes, "PUSHES_PATH",
# tmp)); record/link_reply/act_on_rate read this global at call time so the
# override bites.
PUSHES_PATH = argo_paths.PUSHES_PATH

# How long after a push a user reply still counts as "acting on" it. Default 6h:
# a proactive send the user reads hours later (different timezone, came back from
# away) still links. Past this we assume the reply is about something else.
LINK_WINDOW_SECONDS = 6 * 3600


def _load():
    """Return the push log as a list (empty on missing/corrupt/wrong-shape)."""
    rows = argo_store.load_json(PUSHES_PATH, [])
    return rows if isinstance(rows, list) else []


def record(kind, content, ts=None):
    """Append one push event and return its new id (max existing + 1).

    Stores a sha256 hash of the content rather than the content itself -- we only
    need to distinguish/audit pushes, not re-read their text (chat memory already
    keeps the full text). ts defaults to now.
    """
    if ts is None:
        ts = time.time()
    PUSHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = _load()
    new_id = max((r.get("id", 0) for r in rows), default=0) + 1
    content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    rows.append({
        "id": new_id,
        "ts": ts,
        "kind": kind,
        "content_hash": content_hash,
        "linked": False,
        "linked_ts": None,
    })
    argo_store.save_json(PUSHES_PATH, rows)
    log.info("recorded push id=%d kind=%s", new_id, kind)
    return new_id


def link_reply(chat_id, ts=None):
    """Link a user reply to the most-recent still-open push within the window.

    Finds the newest push that is still unlinked AND whose ts is within
    LINK_WINDOW_SECONDS before `ts`; marks it linked. Returns that push id, or
    None if no open push qualifies. chat_id is accepted for signature parity and
    logging only -- the store is global per the PRD.
    """
    if ts is None:
        ts = time.time()
    rows = _load()
    candidate = None
    for r in rows:
        if r.get("linked"):
            continue
        r_ts = r.get("ts")
        if not isinstance(r_ts, (int, float)):
            continue
        # Within the window: the push came before (or at) the reply, no older
        # than LINK_WINDOW_SECONDS.
        if 0 <= ts - r_ts <= LINK_WINDOW_SECONDS:
            if candidate is None or r_ts > candidate.get("ts"):
                candidate = r
    if candidate is None:
        return None
    candidate["linked"] = True
    candidate["linked_ts"] = ts
    argo_store.save_json(PUSHES_PATH, rows)
    log.info("linked reply (chat_id=%s) to push id=%d", chat_id, candidate.get("id"))
    return candidate.get("id")


def act_on_rate():
    """Return linked_count / total_count over all recorded pushes (0.0 if none)."""
    rows = _load()
    if not rows:
        return 0.0
    linked = sum(1 for r in rows if r.get("linked"))
    return linked / len(rows)
