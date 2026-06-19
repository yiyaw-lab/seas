"""Rating and project-state helpers, extracted from argo_webhook.

These five functions all read or mutate the project log: parse a bare 1-10 into
an energy rating, find which project a rating/SELECT refers to, recognize a paste
of a project Argo already sent, and write a rating or a selection onto it. They
formed one cohesive seam buried in the webhook server, so they live here now --
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


def target_project(log, project_id=None):
    """The project a bare rating/SELECT refers to: an explicit id if given, else
    the one most recently SHOWN to Yiya (delivered, marked shown_at), else the
    last in the log. Using 'last shown' not 'last generated' keeps a rating/SELECT
    attached to the project she's actually looking at, even if a newer one was
    generated after."""
    if project_id:
        return next((p for p in log if p.get("id") == project_id), None)
    shown = [p for p in log if p.get("shown_at")]
    if shown:
        return max(shown, key=lambda p: p["shown_at"])
    return log[-1] if log else None


def match_existing_project(text, projects_log):
    """If `text` looks like a paste of an EXISTING logged project (its pitch or a
    chunk of its body), return that project; else None. Stops a paste of a project
    Argo already sent from being misread as a brand-new idea (add_project)."""
    t = " ".join((text or "").split()).lower()
    if len(t) < 25:  # too short to confidently match; let the LLM handle it
        return None
    log = argo_store.load_json(projects_log, None)
    if not log:
        return None
    for p in reversed(log):  # prefer the most recent match
        body = " ".join(p.get("text", "").split()).lower()
        if not body:
            continue
        # Match if the pasted text is contained in the project (a paste of part of
        # it), or the project's distinctive first line is contained in the paste.
        first_line = body.split(".")[0]
        if (t in body) or (len(first_line) >= 25 and first_line in t):
            return p
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


def target_outcome_project(log, project_id=None):
    """The bet a SHIPPED/DROPPED grades: an explicit id if given, else the most
    recently SELECTED project -- the committed bet whose judgment prediction is
    armed. NOT 'last shown': showing a new candidate after a SELECT must not steal
    the outcome of a bet already in flight (that would grade the wrong belief). Ties
    on the minute-resolution selected_at break toward the later log entry (the more
    recent SELECT). Returns None when nothing has been selected."""
    if project_id:
        return next((p for p in log if p.get("id") == project_id), None)
    selected = [(i, p) for i, p in enumerate(log) if p.get("selected_at")]
    if selected:
        return max(selected, key=lambda ip: (ip[1]["selected_at"], ip[0]))[1]
    return None


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
