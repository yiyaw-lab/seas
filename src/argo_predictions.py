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

# The project-entry fields that hold a committed bet's judgment-prediction ids: the
# project_shipped pred (judgment_prediction_id -- kept for step-1 back-compat) and the
# energy-graded project_mattered pred. Defined here, the module both writers import, so
# the set CANNOT silently diverge between argo_rehearse (records/voids them) and
# argo_rating (arms them). Add a kind here -> both stay in lock-step.
JUDGMENT_PRED_FIELDS = ("judgment_prediction_id", "judgment_mattered_prediction_id")

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


def cancel_many(pred_ids, reason=""):
    """Void several predictions in ONE atomic store write so score_due never grades
    them -- used when a premise that recorded a SET of predictions is retracted together
    (a verdict flip retires both the project_shipped and project_mattered bet at once).
    Atomic by construction: all the still-unscored ids are marked in memory, then a
    SINGLE _save persists them, so a partial failure can never leave one bet voided and
    its sibling live (the save either lands for all or for none). Skips ids that are
    missing or already scored (idempotent). Returns the list of prediction dicts voided."""
    items = _load()
    by_id = {x.get("id"): x for x in items}
    now = _now_iso()
    voided = []
    for pid in pred_ids:
        p = by_id.get(pid)
        if p is None or p.get("scored_at"):
            continue
        p["scored_at"] = now
        p["correct"] = None
        p["voided"] = True
        p["void_reason"] = reason
        voided.append(p)
    if voided:
        _save(items)
        log.info("predictions voided: %s (%s)", [p["id"] for p in voided],
                 reason or "(no reason)")
    return voided


def cancel(pred_id, reason=""):
    """Void a single armed prediction so score_due never grades it -- used when the
    premise that recorded it is retracted (e.g. a re-rehearsal flips SHIP -> KILL, so the
    bet must no longer move the verdict-class belief). Marks it scored with a null
    outcome; the belief is left untouched. Idempotent. Returns it, or None. Thin wrapper
    over the atomic cancel_many."""
    voided = cancel_many([pred_id], reason)
    return voided[0] if voided else None


# --- metric registry: only machine-checkable kinds ---------------------------

# The 1-10 energy at or above which the operator's rating counts the bet as "it
# mattered" (full credit) -- the external, human label the project_mattered kind
# grades against. Shared with argo_calibration so "mattered" means one thing across
# the belief grader and the surfaced calibration number.
MATTERED_ENERGY_MIN = 7

# Recognized metric kinds: a None verdict for one of these is an expected "no
# outcome yet" pending state (logged quietly), not an unknown metric (which warns).
_KNOWN_KINDS = ("incident_absent", "project_shipped", "project_mattered")

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
      {"kind": "project_mattered", "project_id": "P-NNN"}
          the same verdict's energy bet, gradeable ONLY once the bet SHIPPED: True when
          the operator's 1-10 rating lands at/above MATTERED_ENERGY_MIN, False below,
          None while unrated OR not-yet-shipped/dropped. A bet with no shipped artifact
          has nothing to have mattered, so the honest answer is None (abstain) -- never a
          belief move on a phantom outcome, and it keeps this belief coherent with the
          calibration number, which counts a dropped bet as a ship-and-matter miss.
          Graded against the user's OWN rating (an external label), never telemetry Argo
          writes itself.
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
    if kind == "project_mattered":
        entry = _project_entry(metric.get("project_id"))
        if entry is None:
            return None
        # Gradeable only once the bet shipped: a dropped/not-yet-shipped bet has no built
        # artifact to have mattered, so abstain (None) rather than move the belief on a
        # phantom outcome. (Ship-vs-drop is graded by project_shipped; this kind grades
        # whether what SHIPPED was something the operator wanted.)
        if not (entry.get("shipped") or entry.get("shipped_at")):
            return None
        energy = entry.get("energy")
        if not isinstance(energy, (int, float)):
            return None  # shipped but unrated -> did-it-matter unanswered -> abstain
        return energy >= MATTERED_ENERGY_MIN
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
