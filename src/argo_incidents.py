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
    "chat_weakness", "other",
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
            # daily attempt cap bounds how many actually send.
            alert = store.get(_CRITICAL_ALERT_KEY)
            if not isinstance(alert, dict):
                alert = {}
            alert["pending"] = {"kind": kind, "signature": str(signature)[:200], "ts": now}
            store[_CRITICAL_ALERT_KEY] = alert
        _save(store)
        return key
    except Exception:
        log.warning("record_incident failed (kind=%s)", kind, exc_info=True)
        return None


def record_model_failure(signature, raw):
    """Surface a SILENT model failure: a non-empty reply that should have parsed as
    JSON but did not. The JSON-mapper swallow sites (argo_evolve, argo_diagnose) used
    to drop these and return None, so they 'disappeared'; recording a model_failure
    incident makes them visible to the diagnose loop and measurable for the
    structured-outputs prediction. No-op for an empty/whitespace reply -- that is an
    infra/outage failure, surfaced on its own call path -- and never raises (delegates
    to record_incident, which caps the sample and swallows store errors)."""
    text = (raw or "").strip()
    if not text:
        return None
    return record_incident("model_failure", signature, sample=text)


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
            # Scrub secrets at this single chokepoint: get_incidents/detail_report and
            # read_incidents (-> the user) and the diagnose model prompt all read clusters
            # here. `key` stays raw -- it is the internal mark/dedup id; it is redacted
            # separately at the few places it is actually surfaced.
            out.append({**c, "key": key,
                        "samples": [_redact(s) for s in (c.get("samples") or [])],
                        "fingerprint": _redact(c.get("fingerprint", ""))})
        out.sort(key=lambda c: (c.get("count", 0), c.get("last_seen", "")), reverse=True)
        return out
    except Exception:
        log.warning("open_clusters failed", exc_info=True)
        return []


# Secret scrubbers for every projection that leaves the trust boundary. The ledger's
# raw `samples` are exception bodies and the `fingerprint` is a normalized signature;
# both CAN embed a bearer header, an API-key prefix, an email, or a token. They reach
# the chat model and the user via read_incidents (format_for_prompt), get_incidents
# (detail_report) and the diagnose model prompt -- so open_clusters scrubs them once,
# centrally, before any reader sees them.
_REDACT_PATTERNS = (
    # keyword=value -- and absorb an HTTP "Bearer <token>" scheme word between the key
    # and the value, else "Authorization: Bearer <jwt>" would redact only "Bearer".
    re.compile(r"(?i)\b(?:bearer|token|api[_-]?key|secret|password|authorization)\b"
               r"\s*[:=]?\s*(?:bearer\s+)?\S+"),
    re.compile(r"\b(?:sk|pk|gh[posru]|xox[baprs])[-_][A-Za-z0-9_\-]{6,}"),  # token prefixes
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                    # AWS access key id
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),    # email
    re.compile(r"\b[0-9a-fA-F]{24,}\b"),                                    # long hex (sha/token)
)


def _redact(text):
    """Scrub likely secrets/PII from a string before it can reach the chat model."""
    s = str(text)
    for pat in _REDACT_PATTERNS:
        s = pat.sub("<redacted>", s)
    return s


def format_for_prompt(limit=12):
    """REDACTED, chat-safe projection of the incident ledger for the read_incidents
    tool: per open cluster, kind/count/status/fingerprint/first+last-seen and ONE
    secret-scrubbed sample -- never the raw samples array. Lets Argo ground a claim
    about its own operational failures instead of confabulating one. Read-only; never
    raises (returns a plain note on any error)."""
    try:
        clusters = open_clusters(min_count=1, window_hours=24 * 365)
        if not clusters:
            return "No open incidents in the ledger."
        out = []
        for c in clusters[:max(1, int(limit))]:
            samples = c.get("samples") or []
            sample = _redact(str(samples[0]))[:200] if samples else "(none)"
            out.append(
                f"[{c.get('kind')}] count={c.get('count')} status={c.get('status')} "
                f"first={str(c.get('first_seen', '?'))[:10]} "
                f"last={str(c.get('last_seen', '?'))[:10]}\n"
                # The fingerprint is derived from the raw signature and strips only
                # digits/UUIDs/hex/URLs -- NOT emails or token-shaped strings -- so it
                # too must be redacted before it reaches the chat model and the user.
                f"  fingerprint: {_redact(str(c.get('fingerprint', '')))}\n"
                f"  sample: {sample}")
        return "\n".join(out)
    except Exception:
        log.warning("format_for_prompt failed", exc_info=True)
        return "Could not read the incident ledger."


def detail_report(limit=10, min_count=1, window_hours=24 * 14):
    """Operator-facing DETAIL for the open incident clusters: the recent sample error
    text behind each count -- the `<tool>: <message>` strings that carry which tool
    failed and why. The /health rollup and open_clusters' projection drop `samples`,
    so a count is all you see there; this surfaces them so a transient upstream blip
    can be told from a stuck tool. Read-only, never raises. Same broad bar /health
    uses by default (any cluster seen at least once in the last 14 days), worst-first,
    capped at `limit` clusters. The get_incidents MCP tool is a thin wrapper over this."""
    clusters = open_clusters(min_count=min_count, window_hours=window_hours)
    if not clusters:
        return "No open incident clusters."
    lines = []
    for c in clusters[: max(1, limit)]:  # floor at 1 so a stray limit<=0 still shows the worst
        triaged = []
        if c.get("belief_id"):
            triaged.append(f"belief {c['belief_id']}")
        if c.get("pr_number"):
            triaged.append(f"PR #{c['pr_number']}")
        triage = (" [" + ", ".join(triaged) + "]") if triaged else ""
        # The fingerprint is the normalized signature, NOT a hash -- for a tool_error
        # it is "<tool>: <message>", so it carries the failing TOOL NAME. The samples
        # below are bare error text (e.g. _record_tool_error stores str(detail), no
        # name), so without the fingerprint two different failing tools with the same
        # generic message would render identically and you couldn't tell them apart.
        sig = c.get("fingerprint")
        sig = f" [{sig}]" if sig else ""
        lines.append(f"{c.get('kind')}{sig} x{c.get('count', 0)} "
                     f"({c.get('status')}{triage}) last {c.get('last_seen')}")
        for s in (c.get("samples") or []):
            lines.append(f"    - {s}")
    return "\n".join(lines)


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
    """Deliver one queued critical-failure heads-up. Called from the local_loop tick so
    failure-to-human latency drops from ~daily to one cycle.

    NOT called from record_incident: that must never send (send_telegram records
    incidents on delivery failure, so sending from inside record_incident risks
    re-entrancy). `send` is an injected callable(text)->bool seam (the tick passes
    send_telegram.try_send_message), which also keeps this module free of a send-layer
    import cycle. Never raises; returns the text on a delivered send, else None.

    Delivery discipline (each clause fixes a real failure mode):
      - SEND, then RELOAD before the bookkeeping save. `send` may itself write the
        ledger (try_send_message logs a delivery_failure on failure); reloading after
        the send means our save can't clobber that nested write with a stale snapshot.
      - Clear `pending` ONLY on a delivered send. A failed send keeps the alert so the
        next tick retries it -- a critical heads-up shouldn't vanish on one Telegram
        hiccup.
      - Count every ATTEMPT (success or failure) toward the daily cap. That bounds a
        permanently-failing send to MAX attempts/day instead of retrying forever, and
        still caps a flapping failure's successful pings. Past the cap the pending is
        dropped (the incident still rides the daily diagnose funnel)."""
    try:
        store = _load()
        alert = store.get(_CRITICAL_ALERT_KEY)
        if not isinstance(alert, dict) or not alert.get("pending"):
            return None
        today = _now_iso()[:10]
        attempts = 0 if alert.get("date") != today else int(alert.get("attempts_today", 0))
        if attempts >= MAX_CRITICAL_ALERTS_PER_DAY:
            alert["pending"] = None  # exhausted today's attempts; drop so it can't pile up
            alert["date"] = today
            store[_CRITICAL_ALERT_KEY] = alert
            _save(store)
            return None
        sent_pending = alert["pending"]
        text = _critical_alert_text(sent_pending)
        ok = bool(send(text))  # may record a delivery_failure incident on failure
        # Reload AFTER the send so a delivery_failure (or concurrent) write isn't clobbered.
        store = _load()
        alert = store.get(_CRITICAL_ALERT_KEY)
        if not isinstance(alert, dict):
            alert = {"pending": None}
        alert["date"] = today
        alert["attempts_today"] = attempts + 1
        # Clear ONLY if the same alert is still pending. A newer severe incident queued
        # during the send I/O must not be dropped just because the older one delivered;
        # leave it so the next tick delivers it. A failed send also keeps pending (retry).
        if ok and alert.get("pending") == sent_pending:
            alert["pending"] = None
        store[_CRITICAL_ALERT_KEY] = alert
        _save(store)
        return text if ok else None
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
