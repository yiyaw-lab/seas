"""
SEAS V3 — probes: the dead-end memory, and the source failure ledger.

Two distinct things live here, both about ATTENTION rather than belief, which is
why they are kept out of findings/ (pristine = things SEAS believes) and out of
the world model:

1. PROBES — when the synthesis floor reads sources but emits NO finding, it must
   not silently drop the signal. A system that does can't tell "never looked"
   from "looked, found nothing," and re-investigates the same dead ends forever.
   A probe is a tracked record of having looked, WITH the reason (the reason is
   the point: the three outcomes imply opposite next actions).

2. FAILURE LEDGER — per-source fetch failures over time. A single failure is
   never actionable (a 503 is usually a 5-minute outage). Only PERSISTENCE across
   separated retries distinguishes a transient blip from a dead link. The ledger
   tracks that so Argo never drafts a delete-this-feed PR over a server hiccup.

This module is pure logic + a JSON store (data/probes.json). It does NOT open
PRs or fetch anything — should_escalate_source() returns WHAT action is
warranted; the caller (seas_finding.py) wires that to the existing
propose_change self-create path. Keeps network/self-create coupling out of here.

Standard-library only.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import argo_paths

ROOT = Path(__file__).resolve().parent.parent
PROBES_PATH = argo_paths.PROBES_PATH  # single source of truth (see argo_paths)

VALID_OUTCOMES = ("inconclusive", "premature", "unreachable")

# A signal probed 'inconclusive' is skipped on re-encounter for this long (don't
# re-burn budget on a known flat frontier unless it recurs with new evidence).
INCONCLUSIVE_COOLDOWN_DAYS = 30
# 'premature' revisits after this; but after this many premature results in a
# row it converts to 'inconclusive' (the treadmill cap).
PREMATURE_REVISIT_DAYS = 14
PREMATURE_MAX_STREAK = 3

# Failure-ledger escalation: a source must fail with a hard (gone/policy) class
# this many times across at least this long before any action is warranted.
ESCALATE_MIN_FAILURES = 3
ESCALATE_MIN_SPAN_HOURS = 48
# A 'transient' source with zero successes for this long converts to gone-class.
TRANSIENT_TO_GONE_DAYS = 7


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _load():
    if not PROBES_PATH.exists():
        return {"probes": [], "ledger": {}}
    try:
        data = json.loads(PROBES_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return {"probes": [], "ledger": {}}
    data.setdefault("probes", [])
    data.setdefault("ledger", {})
    return data


def _save(data):
    PROBES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBES_PATH.write_text(json.dumps(data, indent=2) + "\n")


# --- Probes (dead-end memory) ------------------------------------------------

def _next_probe_id(probes):
    n = 1 + max((int(p["id"].split("-")[1]) for p in probes
                 if p.get("id", "").startswith("PR-")), default=0)
    return f"PR-{n:03d}"


def _premature_streak(signal_ref, probes):
    """Count consecutive most-recent 'premature' probes for this signal."""
    streak = 0
    for p in reversed(probes):
        if p.get("signal") != signal_ref:
            continue
        if p.get("outcome") == "premature":
            streak += 1
        else:
            break
    return streak


def record_probe(signal_ref, outcome, sources_read, why, cost=None):
    """Record that SEAS investigated a signal and emitted no finding. Applies the
    treadmill cap: the 4th premature-in-a-row is downgraded to inconclusive.
    Returns the stored probe."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    data = _load()
    probes = data["probes"]

    revisit_after = None
    if outcome == "premature":
        if _premature_streak(signal_ref, probes) + 1 >= PREMATURE_MAX_STREAK:
            outcome = "inconclusive"  # converted: stop looking
            why = f"(converted from premature after {PREMATURE_MAX_STREAK} tries) {why}"
        else:
            revisit_after = (datetime.now(timezone.utc)
                             + timedelta(days=PREMATURE_REVISIT_DAYS)).strftime("%Y-%m-%d")

    probe = {
        "id": _next_probe_id(probes),
        "signal": signal_ref,
        "investigated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "outcome": outcome,
        "sources_read": sources_read or [],
        "why": why,
        "revisit_after": revisit_after,
        "cost": cost or {},
    }
    probes.append(probe)
    _save(data)
    return probe


def should_investigate(signal_ref):
    """Check-before-investigate dedup. Returns (bool, reason). Skips a signal
    recently probed inconclusive, or premature whose revisit date hasn't arrived.
    This is the same memory pattern as data/argo_seen.json, applied to
    investigation instead of alerting."""
    data = _load()
    today = datetime.now(timezone.utc).date()
    # Most recent probe for this signal decides.
    last = next((p for p in reversed(data["probes"])
                 if p.get("signal") == signal_ref), None)
    if last is None:
        return True, "never probed"
    if last["outcome"] == "inconclusive":
        investigated = datetime.strptime(last["investigated"], "%Y-%m-%d").date()
        age_days = (today - investigated).days
        if age_days < INCONCLUSIVE_COOLDOWN_DAYS:
            return False, (f"probed inconclusive {age_days}d ago "
                           f"(cooldown {INCONCLUSIVE_COOLDOWN_DAYS}d)")
        return True, "inconclusive cooldown elapsed"
    if last["outcome"] == "premature" and last.get("revisit_after"):
        revisit = datetime.strptime(last["revisit_after"], "%Y-%m-%d").date()
        if today < revisit:
            return False, f"premature; revisit after {last['revisit_after']}"
        return True, "premature revisit date reached"
    return True, "prior probe does not block"


# --- Failure ledger (tooling-health memory) ----------------------------------

def classify_failure(status):
    """Map an HTTP status (or None for timeout/DNS) to a failure class.
    gone = permanently absent; policy = alive but refusing; transient = blip."""
    if status in (404, 410):
        return "gone"
    if status in (401, 403):
        return "policy"
    return "transient"  # 5xx, timeout, DNS, connection errors, unknown


def record_fetch_failure(source, status):
    """Append a failure to a source's ledger. Returns the updated entry."""
    data = _load()
    led = data["ledger"].setdefault(source, {
        "source": source, "failures": [], "first_failed": None, "last_ok": None,
    })
    cls = classify_failure(status)
    now = _now_iso()
    if not led["failures"]:
        led["first_failed"] = now
    led["failures"].append({"ts": now, "status": status, "class": cls})
    _save(data)
    return led


def record_fetch_success(source):
    """A successful fetch CLEARS the ledger. This is what makes a 5-minute outage
    a non-event: the next scheduled fetch wipes the slate before any threshold is
    ever reached."""
    data = _load()
    led = data["ledger"].get(source)
    if led is not None:
        led["failures"] = []
        led["first_failed"] = None
        led["last_ok"] = _now_iso()
        _save(data)


def should_escalate_source(source):
    """Decide whether a source's failures have PERSISTED long enough to warrant a
    self-heal action — and which action. Returns (escalate: bool, action: str,
    reason: str). action is one of: 'remove_or_update' (gone), 'investigate'
    (policy, never delete), or '' (no action).

    Time, not count, is the actionable signal: hard failures must recur >=N times
    across >=48h. Transient failures never escalate directly; they only convert
    to gone-class after a long unbroken dry spell. Two gates total before a feed
    is removed: this time gate, then the human PR review.
    """
    data = _load()
    led = data["ledger"].get(source)
    if not led or not led["failures"]:
        return False, "", "no active failures"

    failures = led["failures"]
    hard = [f for f in failures if f["class"] in ("gone", "policy")]
    span_h = ((_parse(failures[-1]["ts"]) - _parse(failures[0]["ts"]))
              .total_seconds() / 3600.0)

    # Transient-only, but a long unbroken dry spell -> treat as gone.
    if not hard:
        dry_days = ((_parse(failures[-1]["ts"]) - _parse(led["first_failed"]))
                    .total_seconds() / 86400.0)
        if dry_days >= TRANSIENT_TO_GONE_DAYS and len(failures) >= ESCALATE_MIN_FAILURES:
            return True, "remove_or_update", (
                f"transient for {dry_days:.0f}d with no success "
                f"({len(failures)} failures) -> effectively dead")
        return False, "", (f"only transient failures "
                            f"({len(failures)}, {span_h:.0f}h) -> wait")

    if len(hard) < ESCALATE_MIN_FAILURES or span_h < ESCALATE_MIN_SPAN_HOURS:
        return False, "", (f"{len(hard)} hard failures over {span_h:.0f}h "
                           f"(need >={ESCALATE_MIN_FAILURES} over "
                           f">={ESCALATE_MIN_SPAN_HOURS}h)")

    # Persistent hard failure. Class decides the action: gone may be removed;
    # policy (403/401) is ALIVE and refusing -> never delete, investigate/swap.
    if any(f["class"] == "gone" for f in hard):
        return True, "remove_or_update", (
            f"{len(hard)} gone-class failures over {span_h:.0f}h -> dead link")
    return True, "investigate", (
        f"{len(hard)} policy failures over {span_h:.0f}h -> alive but blocking "
        "(do NOT delete; update headers or swap to its feed)")
