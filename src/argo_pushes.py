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
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple

import argo_http
import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# What post_to_webhook hands back. `recorded` = the server accepted+stored the push
# (a 2xx that wasn't suppressed); `suppressed` = the F6 gate refused it, so the
# caller MUST skip the Telegram send. On any POST failure both are False, which is
# fail-open on the SEND: an un-instrumented push is a missed measurement, never a
# silenced send (the caller sends unless `suppressed` is True).
PushResult = namedtuple("PushResult", "recorded suppressed")

# Module-level so tests can patch it (mock.patch.object(argo_pushes, "PUSHES_PATH",
# tmp)); record/link_reply/act_on_rate read this global at call time so the
# override bites.
PUSHES_PATH = argo_paths.PUSHES_PATH
# Steerable-proactiveness threshold store (F6), same patch convention.
PROACTIVE_PATH = argo_paths.PROACTIVE_PATH

# How long after a push a user reply still counts as "acting on" it. Default 6h:
# a proactive send the user reads hours later (different timezone, came back from
# away) still links. Past this we assume the reply is about something else.
LINK_WINDOW_SECONDS = 6 * 3600

# How recently an identical (kind, content) push must have been recorded for a
# repeat record() call to be treated as a re-send of the same push (the at-least-
# once retry) and collapsed to the existing row rather than appended. 5 min easily
# absorbs the ~10s retry; identical content sent hours apart is outside it.
RECORD_DEDUP_SECONDS = 300

# Serializes the read-modify-write of record() and link_reply(). All writers live
# in the one Railway webhook process (Decision_040): the webhook handles Telegram
# updates on background threads and the /push handler writes concurrently in the
# same process, so overlapping load_json -> mutate -> save_json could drop rows,
# collide on max(id)+1, or lose a linked update. An in-process lock is sufficient
# (no cross-process writer); argo_store still does the atomic save underneath.
_write_lock = threading.Lock()


def _load():
    """Return the push log as a list (empty on missing/corrupt/wrong-shape)."""
    rows = argo_store.load_json(PUSHES_PATH, [])
    return rows if isinstance(rows, list) else []


def record(kind, content, ts=None):
    """Record one push event and return its id, idempotent within a short window.

    Stores a sha256 hash of the content rather than the content itself -- we only
    need to distinguish/audit pushes, not re-read their text (chat memory already
    keeps the full text). ts defaults to now.

    Idempotency: the content_hash is over content ONLY (not kind), so within the
    lock -- before appending -- we scan for an existing row with the SAME
    content_hash AND the SAME kind recorded within RECORD_DEDUP_SECONDS of `ts`. If
    one exists this is the at-least-once retry re-POSTing a push the first POST
    already committed (the 2xx was lost to a timeout/reset after commit): we RETURN
    its existing id WITHOUT appending and WITHOUT touching its linked state, so one
    push is one row and act_on_rate's denominator can't double-count it. The
    ~10s retry sits well inside the 5-min window; a genuinely identical re-send
    minutes apart is itself effectively one push event, so collapsing it is correct;
    legitimately distinct identical content sent HOURS apart (e.g. the same watch
    alert on two cron runs) is far outside the window and still records separately.
    Otherwise append a new row with id = max existing + 1.
    """
    if ts is None:
        ts = time.time()
    PUSHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    # Lock the load->scan->mutate->save so a concurrent record/link_reply in the
    # same webhook process can't drop a row, collide on max(id)+1, or race the
    # dedup scan (the scan + append must be one atomic read-modify-write).
    with _write_lock:
        rows = _load()
        # Dedup: an identical (kind, content) push within the window is the retry
        # re-recording an already-committed push -- reuse its id, don't append.
        for r in rows:
            if r.get("content_hash") != content_hash or r.get("kind") != kind:
                continue
            r_ts = r.get("ts")
            if not isinstance(r_ts, (int, float)):
                continue
            if 0 <= ts - r_ts <= RECORD_DEDUP_SECONDS:
                log.info("deduped push (kind=%s) to existing id=%d within %ds",
                         kind, r.get("id"), RECORD_DEDUP_SECONDS)
                return r.get("id")
        new_id = max((r.get("id", 0) for r in rows), default=0) + 1
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


def post_to_webhook(kind, content):
    """Record a push onto the RAILWAY VOLUME by POSTing it to the running webhook.

    Placement triad (the bug this fixes -- recorders ran on the ephemeral Actions
    checkout while the reader runs on the Railway volume, so the two filesystems
    never met and act_on_rate stayed pinned at 0.0):
      trigger     = the proactive send (argo_project.main / argo_watch.main) on
                    GitHub Actions;
      filesystem  = the Railway volume's PUSHES_PATH, written by the /push handler
                    over this authenticated POST -- NOT the local Actions FS;
      consumer    = the webhook reader (link_reply on the inbound user turn, then
                    act_on_rate) on that same volume.

    Best-effort and non-fatal by contract: callers wrap nothing extra -- any
    failure (no WEBHOOK_URL/token in local dev, a timeout, a non-2xx, a network
    error) is logged and swallowed here so a failed POST can never block or fail
    the Telegram send. Returns a PushResult(recorded, suppressed):
      - recorded=True   -> the server stored the push (2xx, gate allowed it);
      - suppressed=True -> the F6 gate (server-side, on the volume where the
        act-on-rate + threshold live) refused the push, so the CALLER MUST SKIP
        the Telegram send -- this is how the gate's send-decision is bridged back
        to the Actions sender, which has neither the rate nor the threshold;
      - both False       -> the POST failed; FAIL-OPEN on the send (an un-recorded
        push is a missed measurement, never a silenced send), so the caller sends.

    One fast bounded retry on a TRANSIENT failure (timeout / network error / 5xx)
    only -- this POST now precedes the Telegram send (record-before-send), so the
    per-attempt timeout is kept short and the retry capped at one so a flaky webhook
    can't noticeably delay delivery. A 4xx (e.g. bad auth) is permanent, not
    retried. The at-least-once hazard the retry introduces (the first POST reaches
    the server and record() commits, but the 2xx is lost to a read timeout / reset
    AFTER commit, so the retry re-POSTs the same content) is SAFE: record() dedupes
    identical (kind, content) within RECORD_DEDUP_SECONDS, so the re-POST returns
    the existing row's id instead of appending a second one -- no double-count in
    act_on_rate's denominator. Residual best-effort limitations that remain by
    design: (1) if the POST fails ALL retries the push stays UNRECORDED while its
    Telegram message was delivered, so a later user reply can link a DIFFERENT
    still-open recorded push within the window and overstate act_on_rate (closing
    this fully would need a per-message reply-attribution engine, out of scope for
    this single honest-rate metric); (2) genuinely distinct identical content sent
    inside the dedup window collapses to one row, which is acceptable -- such a
    re-send is effectively one push event.
    """
    base = os.environ.get("WEBHOOK_URL")
    token = os.environ.get("ARGO_MCP_TOKEN")
    if not base or not token:
        # Local dev / unconfigured Actions: skip silently, no error. Fail-open on
        # the send -- not suppressed, just un-instrumented.
        return PushResult(False, False)
    url = base.rstrip("/") + "/push"
    body = json.dumps({"kind": kind, "content": content}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    ctx = argo_http.tls_context()
    # At most two attempts (one retry). Keep the per-attempt timeout short because
    # this precedes the Telegram send. urlopen RAISES HTTPError (a URLError) for any
    # non-2xx, so success is the no-exception path; failures are classified in the
    # except: retry only TRANSIENT ones (timeout / network error / 5xx), never a
    # permanent 4xx (e.g. bad auth).
    attempts = 2
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                ok = 200 <= r.status < 300
                raw = r.read() if ok else b""
            if ok:
                # A 2xx may be a recorded push OR an F6 suppression. Parse the body
                # to tell them apart; a missing/garbled body is treated as recorded
                # (the old contract), never as a suppression, so a parse hiccup can
                # never silence a send.
                suppressed = False
                try:
                    suppressed = bool(json.loads(raw or b"{}").get("suppressed"))
                except (ValueError, TypeError):
                    pass
                if suppressed:
                    log.info("push kind=%s suppressed by gate (server)", kind)
                else:
                    log.info("posted push kind=%s to webhook volume", kind)
                return PushResult(not suppressed, suppressed)
            log.warning("push POST to webhook returned status %s", r.status)
            return PushResult(False, False)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Non-fatal: the proactive Telegram message will still be sent; an
            # un-instrumented push is a missed measurement, never a failed send.
            log.warning("push POST to webhook failed (non-fatal, attempt %d): %s",
                        attempt + 1, exc)
            code = getattr(exc, "code", None)  # HTTPError carries the HTTP status
            permanent = isinstance(code, int) and 400 <= code < 500
            if permanent or attempt == attempts - 1:
                return PushResult(False, False)
    return PushResult(False, False)


def link_reply(chat_id, ts=None):
    """Link a user reply to the most-recent still-open push within the window.

    Finds the newest push that is still unlinked AND whose ts is within
    LINK_WINDOW_SECONDS before `ts`; marks it linked. Returns that push id, or
    None if no open push qualifies. chat_id is accepted for signature parity and
    logging only -- the store is global per the PRD.
    """
    if ts is None:
        ts = time.time()
    # Lock the load->select->mutate->save so a concurrent record/link_reply in the
    # same webhook process can't lose this linked update or link a row another
    # thread is also mutating.
    with _write_lock:
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


# --- F6: rare / right / steerable proactiveness ---------------------------------
# The PRD gate, un-gated now that F1 makes proactiveness measurable: before an
# unprompted push goes out, score it stakes*confidence and only send when it
# clears a threshold. The threshold is user-TUNABLE (the PROACTIVE command writes
# the base level) and AUTO-DIALS-UP when the recent act-on-rate is low -- if the
# user isn't acting on pushes, raise the bar. The measurement-trap guard the PRD
# insists on: never amplify an unmeasured signal, and at COLD START (too few
# recorded pushes for act_on_rate to mean anything) do NOT raise the bar at all,
# so the very first pushes -- the ones that BUILD the act-on-rate -- are judged on
# their own stakes*confidence, never suppressed for lacking a history.

# The base threshold the user tunes. 0.30 lets a mid-stakes/mid-confidence push
# (0.6 * 0.6 = 0.36) through by default while suppressing genuinely low ones; the
# user dials it via the PROACTIVE command. Clamped to [0, 1].
DEFAULT_THRESHOLD = 0.30

# stakes*confidence defaults per push KIND, used when the caller passes none. A
# weekly project (the user explicitly opted into) and a frontier-builder watch
# alert both clear the default; the map is the seam where a future low-stakes push
# kind (e.g. a chatty nudge) gets a low score and is suppressed first. (stakes,
# confidence) each in [0, 1].
_KIND_DEFAULTS = {
    "project": (0.7, 0.8),  # 0.56
    "watch": (0.6, 0.7),    # 0.42
}
# A push kind we don't recognize is treated as middling, not high -- an unknown
# sender shouldn't get a free pass above the bar.
_UNKNOWN_KIND_SCORE = (0.5, 0.5)  # 0.25, below DEFAULT_THRESHOLD

# How much a zero act-on-rate can raise the threshold, scaled linearly by how low
# the rate is: dial_up = MAX_DIAL_UP * (1 - act_on_rate). At rate 0.0 the bar
# rises by the full MAX_DIAL_UP; at rate 1.0 it doesn't move. Capped so the
# combined threshold never exceeds 1.0 (which would suppress everything).
MAX_DIAL_UP = 0.40

# Cold start: the act-on-rate is only trustworthy once enough pushes have had a
# chance to be acted on. Below this many recorded pushes we apply NO dial-up --
# the bar stays at the user's base level so early pushes aren't strangled by a
# rate computed from too little data (and a divide-by-nothing can't arise:
# act_on_rate already returns 0.0 on an empty store).
MIN_PUSHES_FOR_DIALUP = 5


def _clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def get_threshold():
    """Return the user-tuned base threshold (DEFAULT_THRESHOLD if unset/corrupt)."""
    cfg = argo_store.load_json(PROACTIVE_PATH, {})
    if not isinstance(cfg, dict):
        return DEFAULT_THRESHOLD
    val = cfg.get("threshold")
    if not isinstance(val, (int, float)):
        return DEFAULT_THRESHOLD
    return _clamp01(float(val))


def set_threshold(value):
    """Persist the user's base threshold, clamped to [0, 1]; returns the stored
    value. Raises ValueError on a non-numeric input so the command handler can
    report it rather than writing garbage."""
    threshold = _clamp01(float(value))  # ValueError on non-numeric, by contract
    PROACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(PROACTIVE_PATH, {"threshold": threshold})
    log.info("proactiveness base threshold set to %.2f", threshold)
    return threshold


def effective_threshold():
    """The base threshold plus the auto-dial-up term for a low act-on-rate.

    dial_up = MAX_DIAL_UP * (1 - act_on_rate), applied ONLY once enough pushes
    exist (>= MIN_PUSHES_FOR_DIALUP) for the rate to be meaningful -- the cold-
    start guard. Result clamped to [0, 1]."""
    base = get_threshold()
    rows = _load()
    if len(rows) < MIN_PUSHES_FOR_DIALUP:
        return base  # cold start: trust the base, don't amplify an unmeasured signal
    rate = act_on_rate()
    dial_up = MAX_DIAL_UP * (1.0 - rate)
    return _clamp01(base + dial_up)


def score(kind, stakes=None, confidence=None):
    """stakes*confidence in [0, 1]. Missing stakes/confidence fall back to the
    per-kind default (or a middling unknown-kind score)."""
    if stakes is None or confidence is None:
        ds, dc = _KIND_DEFAULTS.get(kind, _UNKNOWN_KIND_SCORE)
        if stakes is None:
            stakes = ds
        if confidence is None:
            confidence = dc
    return _clamp01(float(stakes)) * _clamp01(float(confidence))


def should_send(kind, stakes=None, confidence=None):
    """Gate one unprompted push: True iff its stakes*confidence score clears the
    effective threshold (base, auto-dialed-up when the recent act-on-rate is low).

    Returns (allowed: bool, reason: str). reason is a short operator-log string;
    callers send no part of it to the user. Never raises -- a scoring/threshold
    error must not block a send, so on an unexpected error we log and ALLOW (fail
    open: the worst case is one un-gated push, never a silenced bot)."""
    try:
        s = score(kind, stakes, confidence)
        thresh = effective_threshold()
        allowed = s >= thresh
        reason = f"score={s:.2f} {'>=' if allowed else '<'} threshold={thresh:.2f} (kind={kind})"
        if allowed:
            log.info("push gate ALLOW: %s", reason)
        else:
            log.info("push gate SUPPRESS: %s", reason)
        return allowed, reason
    except (ValueError, TypeError) as exc:
        log.warning("push gate errored, allowing send (fail-open): %s", exc)
        return True, f"gate error (fail-open): {exc}"
