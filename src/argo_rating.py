"""Rating ACTION helpers, extracted from argo_webhook.

These functions read or mutate the project log: parse a bare 1-10 into an energy
rating, and write a rating / selection / outcome onto a project (arming the bound
judgment predictions). The pure project-IDENTITY lookups they target against --
target_project / target_outcome_project / match_existing_project -- live in
argo_project_state (a separate cohesive seam: identity, no log mutation) and are
imported here so existing argo_rating.* call sites keep working. Both modules are
pure of Telegram, easy to test in isolation.

The functions take the log PATH as an argument rather than reading a module
constant. That's deliberate: argo_webhook keeps thin wrappers (_record_rating,
etc.) that forward its own PROJECTS_LOG global, so the tests that patch
wh.PROJECTS_LOG still drive behavior without this module needing to know about
the override. Stdlib + argo_store for the on-disk format.
"""

import re
from datetime import datetime, timezone

import argo_predictions
import argo_store
from argo_project_state import (
    match_existing_project,
    target_outcome_project,
    target_project,
)


def _arm_judgment_predictions(target):
    """Arm every judgment prediction bound to this committed bet (project_shipped + the
    energy-graded project_mattered). SELECT arms both; the outcome path re-arms both.
    arm() is idempotent, so this is safe to call repeatedly; a store hiccup on one
    prediction must not block arming the other. Reads the field set from argo_predictions
    (the shared source of truth) so it can never drift from what argo_rehearse records."""
    for field in argo_predictions.JUDGMENT_PRED_FIELDS:
        pred_id = target.get(field)
        if pred_id:
            try:
                argo_predictions.arm(pred_id)
            except OSError:
                pass


def parse_rating(text):
    """A bare number 1-10 (integers or decimals like 7.5) is an energy rating.
    Returns a float in [1, 10], or None. Must be the WHOLE message so prose like
    'build 3 things' isn't misread as a rating."""
    m = re.match(r"\s*(\d{1,2}(?:\.\d+)?)\s*$", (text or "").strip())
    if not m:
        return None
    val = float(m.group(1))
    if 1 <= val <= 10:
        return int(val) if val.is_integer() else val
    return None


def record_rating(value, projects_log, project_id=None):
    """Apply a rating to the project Yiya is responding to (last shown), or a
    specific id. Returns a status string."""
    log = argo_store.load_json(projects_log, None)
    if not log:
        return None
    target = target_project(log, project_id)
    if target is None:
        return None
    target["energy"] = value
    target["rated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    argo_store.save_json(projects_log, log)
    return f"Logged energy {value}/10 for {target['id']}. 👍"


def select_latest_project(projects_log, project_id=None):
    """Mark a project as selected. With no id, marks the one most recently SHOWN
    to Yiya (not just the last generated); with an id (e.g. 'SELECT P-002'), marks
    that specific candidate. Returns its id, or None if there's nothing/no match."""
    log = argo_store.load_json(projects_log, None)
    if not log:
        return None
    target = target_project(log, project_id)
    if target is None:
        return None
    target["selected"] = True
    target["selected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    argo_store.save_json(projects_log, log)
    # Arm any judgment predictions already bound to this bet (from a prior standalone
    # REHEARSE): the clock starts now that the user has committed. When SELECT itself
    # triggers the rehearse, the predictions don't exist yet here -- rehearse arms them
    # on the spot since the project is already marked selected. arm() is idempotent.
    _arm_judgment_predictions(target)
    return target.get("id")


def set_project_outcome(projects_log, shipped, project_id=None):
    """Grade a committed bet's outcome -- the human closing the judgment loop. A
    shipped bet earns its SHIP/REVISE verdict-class belief up; a dropped one earns it
    down (the dated prediction recorded at rehearse time is scored against this on the
    next score_due run). Targets the most recently SELECTED bet (or an explicit id),
    never last-shown.

    Returns (id, state): state is 'pending' (a bound prediction will grade this
    outcome), 'scored' (the bound prediction is already graded and locked -- a later
    correction updates the log but does NOT re-grade the belief, since confidence
    never moves by assertion), 'none' (no live judgment prediction is bound -- the bet
    was never rehearsed, or its prediction was voided), or 'uncommitted' (the explicit
    id names a real bet that was never SELECTed -- SELECT is the commit that starts the
    clock, so there is nothing to grade and the log is left untouched). (None, 'none')
    when there is no committed bet to target at all."""
    log = argo_store.load_json(projects_log, None)
    if not log:
        return None, "none"
    target = target_outcome_project(log, project_id)
    if target is None:
        return None, "none"
    # An outcome grades a COMMITTED bet; SELECT is the commit that arms the clock. A bare
    # outcome already targets the SELECTED bet (target_outcome_project), but an explicit
    # SHIPPED/DROPPED P-NNN can name a never-selected bet -- refuse it (no ship/drop mark,
    # no arming, no grading), so a belief can never move for a bet that was never bet on.
    if not target.get("selected_at"):
        return target.get("id"), "uncommitted"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if shipped:
        target["shipped"] = True
        target["shipped_at"] = stamp
        target.pop("dropped", None)
        target.pop("dropped_at", None)
    else:
        target["dropped"] = True
        target["dropped_at"] = stamp
        target.pop("shipped", None)
        target.pop("shipped_at", None)
    argo_store.save_json(projects_log, log)
    pred_id = target.get("judgment_prediction_id")
    p = argo_predictions.get_prediction(pred_id) if pred_id else None
    if p is None or p.get("voided"):
        # No LIVE prediction: unrehearsed, no record, or the bound id points at a voided
        # prediction (a verdict flip retired it). A voided pred carries scored_at but
        # never graded a belief, so it must read as 'none' (not 'scored'); otherwise the
        # human outcome would be falsely reported locked and skip re-arming.
        return target.get("id"), "none"
    if p.get("scored_at"):
        return target.get("id"), "scored"         # genuinely graded and locked
    # Bound + unscored: ensure both judgment predictions are armed so the reported
    # "pending" grade actually happens. arm() is idempotent (a no-op when SELECT already
    # armed them); this also recovers the rare case where a SELECT-time arm was lost to
    # a store hiccup. The "pending" state tracks the project_shipped pred (above); the
    # energy-graded project_mattered pred is armed alongside.
    _arm_judgment_predictions(target)
    return target.get("id"), "pending"
