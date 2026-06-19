"""Dated, falsifiable predictions with machine scoring -- the deferred H0.3-H0.5 loop.

SEAS findings and world-model beliefs have always CARRIED predictions ("this will be
true by date X"), but nothing ever came back to grade them, so confidence never earned
the big +-0.20 move that world_model.apply_prediction_outcome reserves for reality.
This module is that grader, built first for the frontier-evolution loop (argo_evolve):
an adopted upgrade records a prediction at accept time, the prediction is ARMED when
the PR merges (the clock starts at deploy, not at talk), and once the due date passes
score_due() evaluates it against the incident ledger and moves the belief.

The honesty rule: a prediction is only scored when its metric is MACHINE-CHECKABLE
(metric registry below). An unknown metric is logged and left unscored -- never
fabricated. Unscorable benefits (cost, vibes) belong in belief evidence, not here.

Standard-library + the shared-utils layer. JSON store at data/argo_predictions.json
(gitignored; ARGO_PREDICTIONS_PATH points it at the Railway volume).
"""

from datetime import datetime, timedelta, timezone

import argo_incidents
import argo_paths
import argo_store
import world_model
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported so tests patch the module global (mock.patch.object); helpers read the
# bare name at call time so the override bites.
PREDICTIONS_PATH = argo_paths.PREDICTIONS_PATH
# Re-exported for the same patch-the-global reason as PREDICTIONS_PATH: the
# project-outcome metric reads the project log at score time, and tests override
# this to a tmp path (mock.patch.object(argo_predictions, "PROJECTS_LOG", ...)).
PROJECTS_LOG = argo_paths.PROJECTS_LOG

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().strftime(_TS_FMT)


def _parse_ts(ts):
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load():
    items = argo_store.load_json(PREDICTIONS_PATH, [])
    return items if isinstance(items, list) else []


def _save(items):
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(PREDICTIONS_PATH, items)


def _next_id(items):
    nums = []
    for p in items:
        pid = str(p.get("id", ""))
        if pid.startswith("EVP-") and pid.split("-", 1)[1].isdigit():
            nums.append(int(pid.split("-", 1)[1]))
    return f"EVP-{max(nums, default=0) + 1:03d}"


def record(belief_id, claim, metric, days, source=""):
    """Register an UNARMED prediction tied to a world-model belief. The clock does
    not start here -- arm() starts it at merge time, so 'within N days' means N days
    of the change actually being live. Returns the prediction id."""
    items = _load()
    pid = _next_id(items)
    items.append({
        "id": pid, "belief_id": belief_id, "claim": (claim or "").strip(),
        "metric": metric if isinstance(metric, dict) else {},
        "days": int(days), "created_at": _now_iso(),
        "armed_at": None, "due": None, "scored_at": None, "correct": None,
        "source": source,
    })
    _save(items)
    log.info("prediction recorded %s (belief %s, due %s days after arming)",
             pid, belief_id, days)
    return pid


def arm(pred_id, merged_at_iso=None):
    """Start the prediction clock: due = merged_at + days. Idempotent (re-arming a
    prediction is a no-op, so a sync pass can call this safely every day). Returns
    the prediction or None."""
    items = _load()
    p = next((x for x in items if x.get("id") == pred_id), None)
    if p is None:
        return None
    if p.get("armed_at"):
        return p
    base = _parse_ts(merged_at_iso) or _now()
    p["armed_at"] = base.strftime(_TS_FMT)
    p["due"] = (base + timedelta(days=int(p.get("days", 14)))).strftime(_TS_FMT)
    _save(items)
    log.info("prediction %s armed; due %s", pred_id, p["due"])
    return p


def get_prediction(pred_id):
    return next((x for x in _load() if x.get("id") == pred_id), None)


def cancel(pred_id, reason=""):
    """Void an armed prediction so score_due never grades it -- used when the premise
    that recorded it is retracted (e.g. a re-rehearsal flips SHIP -> KILL, so the bet
    must no longer move the verdict-class belief). Marks it scored with a null
    outcome; the belief is left untouched. Idempotent. Returns it, or None."""
    items = _load()
    p = next((x for x in items if x.get("id") == pred_id), None)
    if p is None or p.get("scored_at"):
        return None
    p["scored_at"] = _now_iso()
    p["correct"] = None
    p["voided"] = True
    p["void_reason"] = reason
    _save(items)
    log.info("prediction %s voided: %s", pred_id, reason or "(no reason)")
    return p


# --- metric registry: only machine-checkable kinds ---------------------------

# Recognized metric kinds: a None verdict for one of these is an expected "no
# outcome yet" pending state (logged quietly), not an unknown metric (which warns).
_KNOWN_KINDS = ("incident_absent", "project_shipped")

def _project_entry(project_id):
    """The project-log entry for `project_id`, or None. Reads the bare PROJECTS_LOG
    name at call time so a test override of the global bites (mirrors _load)."""
    if not project_id:
        return None
    items = argo_store.load_json(PROJECTS_LOG, None)
    if not isinstance(items, list):
        return None
    return next((p for p in items if p.get("id") == project_id), None)


def _evaluate(metric, armed_at):
    """True/False verdict for a metric, or None when the kind isn't machine-checkable
    (the caller leaves it unscored -- never fabricate an outcome).

    v1 kinds (incident-ledger backed):
      {"kind": "incident_absent", "key": "<cluster key>"}        -- that exact cluster
      {"kind": "incident_absent", "incident_kind": "<kind>"}     -- any cluster of kind

    v2 kinds (project-log backed, HUMAN-graded -- the build-decision loop):
      {"kind": "project_shipped", "project_id": "P-NNN"}
          a SHIP/REVISE verdict's bet: True once the human marks it shipped, False
          once they mark it dropped, None while unreported. The None is the honest
          abstention -- an unreported bet is unknown, never a fabricated miss.
    """
    kind = (metric or {}).get("kind")
    if kind == "incident_absent":
        key = metric.get("key")
        if key:
            return not argo_incidents.recurred_since(key, armed_at)
        ik = metric.get("incident_kind")
        if ik:
            return not argo_incidents.seen_since(ik, armed_at)
    if kind == "project_shipped":
        entry = _project_entry(metric.get("project_id"))
        if entry is None:
            return None
        if entry.get("shipped") or entry.get("shipped_at"):
            return True
        if entry.get("dropped"):
            return False
        return None
    return None


def score_due(notify=None):
    """Score every armed, due, unscored prediction and move its belief's confidence
    via world_model.apply_prediction_outcome (+-0.20 -- the strongest legitimate
    mover, exercised here for the first time). notify: optional callable taking one
    plain-text line per scored prediction (best-effort). Returns the scored items."""
    items = _load()
    now = _now_iso()
    scored = []
    changed = False
    for p in items:
        if p.get("scored_at") or not p.get("armed_at") or not p.get("due"):
            continue
        if p["due"] > now:
            continue
        verdict = _evaluate(p.get("metric"), p["armed_at"])
        if verdict is None:
            kind = (p.get("metric") or {}).get("kind")
            if kind in _KNOWN_KINDS:
                # Known kind, outcome not in yet (e.g. a bet not yet reported
                # shipped/dropped). Expected -- stay quiet and re-check next run.
                log.debug("predictions: %s (%s) has no outcome yet; leaving "
                          "unscored", p.get("id"), kind)
            else:
                log.warning("predictions: metric on %s not machine-checkable; "
                            "leaving unscored", p.get("id"))
            continue
        p["scored_at"] = now
        p["correct"] = bool(verdict)
        changed = True
        world_model.apply_prediction_outcome(p.get("belief_id"), p["id"], bool(verdict))
        log.info("prediction %s scored: %s", p.get("id"),
                 "held" if verdict else "did not hold")
        scored.append(p)
        if notify:
            try:
                word = "held" if verdict else "did not hold"
                notify(f"scoring one of my own predictions: {p.get('claim', '(no claim)')} "
                       f"it {word}, so i've moved my confidence accordingly.")
            except Exception:
                log.warning("predictions: notify failed", exc_info=True)
    if changed:
        _save(items)
    return scored
