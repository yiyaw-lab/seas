"""Argo's operational-failure ledger: the 'observe' layer of the self-improvement loop.

Every operational failure used to go only to stdout (Railway/Actions console) and was
forgotten -- so the human was the only failure detector. This module turns those
ephemeral log lines into durable, deduplicated data Argo can read back and reason
about, which is what lets the daily `diagnose` pass surface its own recurring problems
proactively instead of waiting to be told.

The store is a ROLLUP MAP keyed by `kind|fingerprint`, not an append log: a flapping
failure increments a count in place, so a single broken dependency can't flood the
ledger (the structural guard against self-flooding). The fingerprint strips the volatile
parts of a signature (digits, UUIDs, URLs) so 'sendMessage failed: 503 at 14:03' and
'sendMessage failed: 502 at 17:55' collapse into one cluster.

Inviolable contract: record_incident is called from inside other modules' failure
handlers, so it MUST NEVER raise -- a bug here can't be allowed to turn a logged failure
into a crashed chat turn or scheduler run. Every public function swallows store errors.

Status lifecycle of a cluster: open -> diagnosed -> proposed -> resolved, with `muted`
(user said IGNORE) as a side state. A resolved problem that happens again is, by
definition, open again: record_incident flips resolved->open on recurrence (the ledger
is its own post-deploy verifier -- a fix that didn't hold reappears here).

Standard-library + the shared-utils layer (argo_store/argo_paths/argo_log). JSON store at
data/argo_incidents.json (gitignored; ARGO_INCIDENTS_PATH points it at the Railway volume).
"""

import re
from datetime import datetime, timedelta, timezone

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported so tests can patch the module global (mock.patch.object(argo_incidents,
# "INCIDENTS_PATH", tmp)); helpers read the bare name at call time so the override bites.
INCIDENTS_PATH = argo_paths.INCIDENTS_PATH

# Fixed kinds so a typo can't spawn a phantom bucket; an unknown kind coerces to "other"
# (typos collapse into one harmless cluster instead of proliferating).
INCIDENT_KINDS = frozenset({
    "phantom_send", "phantom_claim", "budget_exceeded", "model_failure",
    "tool_error", "circuit_open", "scheduler_task_error", "delivery_failure",
    "other",
})

MAX_SAMPLES = 3        # keep the few most recent example messages per cluster
SAMPLE_CHARS = 240     # cap each sample so the ledger stays small
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Severe kinds worth a same-day proactive heads-up (not just the daily diagnose):
# the breaker tripping or the budget capping means Argo is partly down NOW. Recording
# one of these queues a _critical_alert flag that the local_loop tick delivers; the
# send NEVER happens here (record_incident must never send -- send_telegram records
# incidents on delivery failure, so sending from here risks re-entrancy/storm).
CRITICAL_ALERT_KINDS = frozenset({"circuit_open", "budget_exceeded"})
MAX_CRITICAL_ALERTS_PER_DAY = 3   # spam guard on a flapping severe failure
_CRITICAL_ALERT_KEY = "_critical_alert"


def _now_iso():
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _parse(ts):
    """Parse a stored timestamp to an aware datetime, or None if unparseable."""
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load():
    store = argo_store.load_json(INCIDENTS_PATH, {})
    return store if isinstance(store, dict) else {}


def _save(store):
    INCIDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(INCIDENTS_PATH, store)


def _fingerprint(signature):
    """Collapse a signature to a stable key by stripping the volatile parts -- URLs,
    UUIDs, long hex (SHAs), and any digit run -- so near-identical failures roll up."""
    s = (signature or "").strip().lower()
    s = re.sub(r"https?://\S+", "<url>", s)
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", s)
    s = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", s)
    s = re.sub(r"\d+", "<n>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80] or "unspecified"


def record_incident(kind, signature, sample=""):
    """Record one operational failure into the rollup ledger. Increments the matching
    cluster's count (creating it if new), refreshes last_seen, and keeps up to the few
    most recent samples. A recurrence of a resolved cluster reopens it (post-deploy proof
    a fix did not hold). NEVER raises: returns the cluster key, or None on any store error."""
    try:
        if kind not in INCIDENT_KINDS:
            log.warning("record_incident: unknown kind %r coerced to 'other'", kind)
            kind = "other"
        fp = _fingerprint(signature)
        key = f"{kind}|{fp}"
        store = _load()
        now = _now_iso()
        c = store.get(key)
        if not isinstance(c, dict):
            c = {"kind": kind, "fingerprint": fp, "count": 0,
                 "first_seen": now, "last_seen": now, "samples": [],
                 "status": "open", "belief_id": None, "pr_number": None,
                 "resolved_commit": None, "muted_until": None}
            store[key] = c
        c["count"] = int(c.get("count", 0)) + 1
        c["last_seen"] = now
        if sample:
            c["samples"] = ([str(sample)[:SAMPLE_CHARS]] + list(c.get("samples", [])))[:MAX_SAMPLES]
        # A resolved problem that recurs is open again. Mark recurred_after_fix so the
        # diagnostic ranking can float it to the top, and keep belief_id/pr_number so
        # re-diagnosis adds refuting evidence to the SAME belief.
        if c.get("status") == "resolved":
            c["status"] = "open"
            c["recurred_after_fix"] = True
        # A muted cluster whose mute window has passed becomes eligible again.
        elif c.get("status") == "muted":
            mu = c.get("muted_until")
            if mu and mu < now:
                c["status"] = "open"
        if kind in CRITICAL_ALERT_KINDS:
            # Queue a same-day heads-up for the local_loop tick (delivery, capping, and
            # clearing all live in drain_critical_alert -- NOT here). Latest wins; the
            # daily cap bounds how many actually send.
            alert = store.get(_CRITICAL_ALERT_KEY)
            if not isinstance(alert, dict):
                alert = {"pending": None, "date": "", "sent_today": 0}
            alert["pending"] = {"kind": kind, "signature": str(signature)[:200], "ts": now}
            store[_CRITICAL_ALERT_KEY] = alert
        _save(store)
        return key
    except Exception:
        log.warning("record_incident failed (kind=%s)", kind, exc_info=True)
        return None


def open_clusters(min_count=3, window_hours=24):
    """Eligible clusters for diagnosis: status 'open', count >= min_count, last seen
    within window_hours. Ranked worst-first by count, then recency. Free, read-only,
    never raises (returns [] on error)."""
    try:
        store = _load()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        out = []
        for key, c in store.items():
            if key.startswith("_") or not isinstance(c, dict):
                continue
            if c.get("status") != "open" or int(c.get("count", 0)) < min_count:
                continue
            seen = _parse(c.get("last_seen", ""))
            if seen is None or seen < cutoff:
                continue
            out.append({**c, "key": key})
        out.sort(key=lambda c: (c.get("count", 0), c.get("last_seen", "")), reverse=True)
        return out
    except Exception:
        log.warning("open_clusters failed", exc_info=True)
        return []


def get_cluster(key):
    c = _load().get(key)
    return c if isinstance(c, dict) else None


def mark(key, status=None, **fields):
    """Update a cluster's status and/or arbitrary fields (belief_id, pr_number,
    muted_until, ...). Returns the updated cluster or None. Never raises."""
    try:
        store = _load()
        c = store.get(key)
        if not isinstance(c, dict):
            return None
        if status is not None:
            c["status"] = status
        for k, v in fields.items():
            c[k] = v
        _save(store)
        return c
    except Exception:
        log.warning("mark failed (%s)", key, exc_info=True)
        return None


def recurred_since(key, since_iso):
    """True if the cluster has been seen strictly after since_iso -- the post-deploy
    recurrence check (last_seen advances on every record_incident)."""
    c = get_cluster(key)
    return bool(c and (c.get("last_seen") or "") > (since_iso or ""))


def seen_since(kind, since_iso):
    """True if ANY cluster of `kind` has been seen strictly after since_iso -- the
    kind-level recurrence check (recurred_since is the key-level one). Used by the
    prediction scorer (argo_predictions) to grade 'this class of failure stays gone'
    claims. Read-only; never raises (False on any store error)."""
    try:
        for key, c in _load().items():
            if key.startswith("_") or not isinstance(c, dict):
                continue
            if c.get("kind") == kind and (c.get("last_seen") or "") > (since_iso or ""):
                return True
        return False
    except Exception:
        log.warning("seen_since failed (%s)", kind, exc_info=True)
        return False


def get_meta(meta_key, default=None):
    """Read a reserved bookkeeping value (keys are namespaced with a leading '_', so
    they never collide with a cluster key)."""
    return _load().get(meta_key, default)


def set_meta(meta_key, value):
    try:
        store = _load()
        store[meta_key] = value
        _save(store)
    except Exception:
        log.warning("set_meta failed (%s)", meta_key, exc_info=True)


def _critical_alert_text(pending):
    """Argo's voice for a proactive failure heads-up: plain text, lowercase, honest,
    no markdown/em dashes. Tells the owner what just broke and that it's pre-emptive."""
    kind = pending.get("kind")
    sig = (pending.get("signature") or "").strip()
    lead = {
        "circuit_open": "heads up: my circuit breaker just tripped",
        "budget_exceeded": "heads up: i just hit my daily call budget",
    }.get(kind, "heads up: something of mine just failed")
    tail = f" ({sig})" if sig else ""
    return (lead + tail + ". flagging it now instead of waiting for the daily check. "
            "nothing for you to do unless it keeps happening.")


def drain_critical_alert(send):
    """Deliver at most one queued critical-failure heads-up, then clear it. Called from
    the local_loop tick so failure-to-human latency drops from ~daily to one cycle.

    NOT called from record_incident: that must never send (send_telegram records
    incidents on delivery failure, so sending from inside record_incident risks
    re-entrancy). `send` is an injected callable(text)->bool seam (the tick passes
    send_telegram.try_send_message), which also keeps this module free of a send-layer
    import cycle. Daily-capped; clears the flag whether or not it sent (a capped day
    drops the ping silently -- the incident still rides the daily diagnose funnel).
    Never raises; returns the text sent, or None."""
    try:
        store = _load()
        alert = store.get(_CRITICAL_ALERT_KEY)
        if not isinstance(alert, dict) or not alert.get("pending"):
            return None
        today = _now_iso()[:10]
        if alert.get("date") != today:
            alert["date"] = today
            alert["sent_today"] = 0
        text = None
        if int(alert.get("sent_today", 0)) < MAX_CRITICAL_ALERTS_PER_DAY:
            text = _critical_alert_text(alert["pending"])
            send(text)  # best-effort; clear regardless to avoid a per-tick retry storm
            alert["sent_today"] = int(alert.get("sent_today", 0)) + 1
        alert["pending"] = None
        store[_CRITICAL_ALERT_KEY] = alert
        _save(store)
        return text
    except Exception:
        log.warning("drain_critical_alert failed", exc_info=True)
        return None


def prune(max_age_days=14):
    """Drop resolved/muted clusters whose last activity is older than max_age_days.
    Open/diagnosed/proposed clusters and reserved meta keys are always kept."""
    try:
        store = _load()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        dropped = [k for k, c in store.items()
                   if not k.startswith("_") and isinstance(c, dict)
                   and c.get("status") in ("resolved", "muted")
                   and (_parse(c.get("last_seen", "")) or datetime.now(timezone.utc)) < cutoff]
        for k in dropped:
            del store[k]
        if dropped:
            _save(store)
        return len(dropped)
    except Exception:
        log.warning("prune failed", exc_info=True)
        return 0
