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

import argo_cost
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
# Re-exported for the same patch-the-global reason: the finding_prediction metric
# reads the finding JSON at score time to find the human verdict, and tests
# override this to a tmp dir (mock.patch.object(argo_predictions, "FINDINGS_DIR", ...)).
FINDINGS_DIR = argo_paths.FINDINGS_DIR

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
    prediction is a no-op, so a sync pass can call this safely every day). A SETTLED
    prediction -- already scored, or voided (cancel_many also sets scored_at) -- is never
    (re)armed: arming it would put an armed clock on a pred that can never grade, and a
    stale binding can point an arm() call at a voided pred. Returns the prediction or None."""
    items = _load()
    p = next((x for x in items if x.get("id") == pred_id), None)
    if p is None:
        return None
    if p.get("scored_at") or p.get("armed_at"):
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
_KNOWN_KINDS = ("incident_absent", "project_shipped", "project_mattered",
                "cache_ratio", "finding_prediction")

def _project_entry(project_id):
    """The project-log entry for `project_id`, or None. Reads the bare PROJECTS_LOG
    name at call time so a test override of the global bites (mirrors _load)."""
    if not project_id:
        return None
    items = argo_store.load_json(PROJECTS_LOG, None)
    if not isinstance(items, list):
        return None
    return next((p for p in items if p.get("id") == project_id), None)


def _finding_entry(finding_id):
    """The finding JSON for `finding_id`, or None. Reads FINDINGS_DIR/<fid>.json at
    call time (bare name) so a test override of the global bites (mirrors _load)."""
    if not finding_id:
        return None
    return argo_store.load_json(FINDINGS_DIR / f"{finding_id}.json", None)


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

    v3 kinds (cost-ledger backed -- the first MEASURED cost claim, ROADMAP Stage 2):
      {"kind": "cache_ratio", "min_ratio": 0.50, "min_calls": 20,
       "model": "<m>"?, "provider": "<p>"?, "label_prefix": "chat/"?}
          a dated prompt-caching savings claim: True once the actual fraction of
          billable input tokens served from cache (argo_cost.cache_input_ratio over
          the rows logged SINCE this prediction was armed) reaches min_ratio across
          at least min_calls calls, False once the window has enough calls but the
          ratio falls short, None while the ledger is too thin to judge (fewer than
          min_calls, or no measurable tokens). The None is the honest abstention --
          an unscorable metric stays unscored, never a guessed pass. Reads the same
          volume ledger argo_cost writes (placement: score_due rides the daily
          'frontier' LOCAL_COMMAND on the webhook's Railway volume, where the cost
          rows live), so it needs ZERO new scheduler wiring.

    v4 kinds (finding-backed, HUMAN-graded -- the research-judgment loop, ROADMAP
    Stage 2: 'every SEAS finding carries a dated prediction that gets scored'):
      {"kind": "finding_prediction", "finding_id": "F-NNN"}
          a SEAS finding's dated prediction (the falsifiable claim + refutation
          condition the emission gate already requires). The finding's `checkable`
          and `refutation_condition` are free-text prose (e.g. 'open the PDFs and
          verify the results table'), which NO store Argo keeps can settle
          mechanically -- so, exactly like project_mattered, this grades against an
          EXTERNAL human verdict stamped onto the finding JSON, never telemetry Argo
          writes about itself: True once the finding records prediction_outcome
          'held', False on 'refuted', None while unjudged. The None is the honest
          abstention -- an unjudged finding is unknown, never a fabricated pass.
          Reads the same findings/ dir seas_finding writes (placement: score_due
          rides the daily 'frontier' LOCAL_COMMAND on the webhook's volume, where
          the findings live), so it needs ZERO new scheduler wiring.
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
    if kind == "cache_ratio":
        try:
            min_ratio = float(metric.get("min_ratio"))
        except (TypeError, ValueError):
            return None  # malformed claim -> abstain (warns as not-checkable below)
        min_calls = metric.get("min_calls", 1)
        min_calls = int(min_calls) if isinstance(min_calls, (int, float)) else 1
        # Measure only the window AFTER arming, so the claim grades the change that
        # was live, not the pre-caching baseline.
        since = _parse_ts(armed_at)
        since_ts = since.timestamp() if since else None
        ratio, calls = argo_cost.cache_input_ratio(
            since_ts=since_ts, model=metric.get("model"),
            provider=metric.get("provider"), label_prefix=metric.get("label_prefix"))
        # Honest insufficient-data: too few calls, or nothing measurable -> abstain.
        if ratio is None or calls < min_calls:
            return None
        return ratio >= min_ratio
    if kind == "finding_prediction":
        finding = _finding_entry(metric.get("finding_id"))
        if not isinstance(finding, dict):
            return None  # finding gone/unreadable -> abstain
        outcome = finding.get("prediction_outcome")
        if outcome == "held":
            return True
        if outcome == "refuted":
            return False
        return None  # unjudged -> abstain (never a guessed pass)
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


# --- arming a concrete cost prediction ---------------------------------------

def arm_cost_prediction(min_ratio=0.50, days=14, min_calls=20,
                        model=None, provider=None, label_prefix="chat/"):
    """Arm ONE concrete, dated prompt-caching savings claim so the milestone can
    fire. Creates (or reuses) a world-model belief that caching cut chat input
    spend, records a `cache_ratio` prediction bound to it, and ARMS it now -- the
    clock starts immediately because the cost telemetry (PR #41) is already live,
    so 'within `days` days' means `days` of real logged traffic. The prediction
    grades only the rows logged AFTER arming, against the >= min_ratio claim across
    at least min_calls calls; until that many calls accrue it stays honestly
    unscored (never a guessed pass). Returns the prediction id.

    Default scope is the chat path (label_prefix='chat/') -- the path PR #30's
    caching actually targets -- across all models/providers. NO ledger data is
    fabricated; this only registers the claim. Idempotent on the belief (add_belief
    dedupes the claim text); each call records a fresh prediction."""
    pct = int(round(min_ratio * 100))
    claim = (f"Prompt caching serves at least {pct}% of chat input tokens from "
             f"cache (measured over {days} days of live traffic).")
    belief_id = world_model.add_belief(claim, source_finding="cost-telemetry:PR#41")
    metric = {"kind": "cache_ratio", "min_ratio": float(min_ratio),
              "min_calls": int(min_calls), "label_prefix": label_prefix}
    if model:
        metric["model"] = model
    if provider:
        metric["provider"] = provider
    pid = record(belief_id, claim, metric, int(days), source="cost-telemetry")
    arm(pid)  # clock starts now -- telemetry is already recording
    log.info("armed cost prediction %s (belief %s): >= %d%% cache over %d days",
             pid, belief_id, pct, days)
    return pid


def main(argv=None):
    """Minimal CLI to arm the concrete cost prediction (mirrors how levers arm
    theirs in argo_evolve, but operator-invoked):

        python3 src/argo_predictions.py arm-cost [--min-ratio 0.5] [--days 14]
                [--min-calls 20] [--model M] [--provider P] [--label-prefix chat/]
    """
    import argparse

    parser = argparse.ArgumentParser(prog="argo_predictions")
    sub = parser.add_subparsers(dest="cmd")
    a = sub.add_parser("arm-cost", help="arm one dated prompt-caching savings claim")
    a.add_argument("--min-ratio", type=float, default=0.50)
    a.add_argument("--days", type=int, default=14)
    a.add_argument("--min-calls", type=int, default=20)
    a.add_argument("--model", default=None)
    a.add_argument("--provider", default=None)
    a.add_argument("--label-prefix", default="chat/")
    args = parser.parse_args(argv)
    if args.cmd == "arm-cost":
        pid = arm_cost_prediction(
            min_ratio=args.min_ratio, days=args.days, min_calls=args.min_calls,
            model=args.model, provider=args.provider, label_prefix=args.label_prefix)
        print(f"armed cost prediction {pid} (due in {args.days} days)")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
