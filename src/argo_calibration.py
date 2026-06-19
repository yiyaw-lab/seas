"""Per-verdict-class judgment calibration -- the cofounder "how reliable is my SHIP
call FOR YOU" number, computed over the operator's own committed-and-graded bets.

Move #3 of the judgment-grounding work (docs/plans/2026-06-18-argo-cofounder-strategy):
once a Rehearse verdict is bound to a scored prediction (argo_predictions) and a human
grades ship-vs-drop and rates energy, this reads those outcomes back as one legible
number -- "when Argo says SHIP, it ships N of M times." The differentiator is that it is
computed over ONE operator's confounded outcomes, where THEIR energy rating is the
human-calibration label: a per-user reliability curve no multi-tenant eval averaging
strangers can fabricate.

Two honesty rails, both IN CODE, not in caveats (strategy doc anti-goal: "the gate, not
the disclaimer, keeps it honest"):
  - n-floor: a verdict class below CALIBRATION_MIN_N committed, graded bets is OMITTED
    from the result entirely -- a thin "4 of 7" presented as a track record is the
    overconfidence this instrument exists to cure, so it is structurally un-surfaceable,
    not merely disclaimered.
  - honest abstention: a bet whose ship/drop outcome is not yet known is EXCLUDED, never
    counted as a miss -- the same never-fabricate rule the grader (_evaluate) uses.

Pure: a function of the project-log list its callers already hold (argo_self.gather_
performance, argo_rehearse._summary_line). No I/O, no path global -- nothing to patch.
"""

from argo_predictions import MATTERED_ENERGY_MIN

# n-floor IN CODE (see module docstring): below this many committed, graded bets a
# verdict class is omitted from the calibration entirely. Four is roughly a month of
# committed bets at the operator's ~1-ship/week pace -- enough for a directional read,
# below which a number reads as a track record it has not earned.
CALIBRATION_MIN_N = 4

# Verdict classes that earn a calibration number (a KILL bet is refused, never built).
_GRADED_VERDICTS = ("SHIP", "REVISE")


def _shipped(entry):
    return bool(entry.get("shipped") or entry.get("shipped_at"))


def _bet_score(entry):
    """Partial-credit score for ONE committed, graded bet, or None when its outcome is
    not yet known (honest abstention -- the caller excludes it). Shipped AND the operator
    rated it high-energy = 1.0 (it shipped and mattered); shipped but low or un-rated
    energy = 0.5 (it shipped, did not clearly matter); dropped = 0.0."""
    if _shipped(entry):
        energy = entry.get("energy")
        mattered = isinstance(energy, (int, float)) and energy >= MATTERED_ENERGY_MIN
        return 1.0 if mattered else 0.5
    if entry.get("dropped"):
        return 0.0
    return None  # outcome unknown -> not graded yet


def compute_calibration(projects):
    """Per-verdict-class calibration over the bets Argo rehearsed to a SHIP/REVISE
    verdict and the operator then SELECTed (committed) and graded. Returns
    {verdict: {"n", "shipped", "rate", "score"}} containing ONLY classes at or above
    CALIBRATION_MIN_N -- a class below the n-floor is absent from the result, so a
    caller cannot surface a too-thin number even by accident.

      n       -- committed, graded bets in the class (the honest denominator)
      shipped -- how many of them shipped (the "N of M" headline numerator)
      rate    -- shipped / n
      score   -- mean partial credit (1.0 shipped+mattered, 0.5 shipped, 0.0 dropped)
    """
    buckets = {}
    for p in (projects or []):
        if not isinstance(p, dict):
            continue
        verdict = p.get("verdict")
        # Only a committed bet (selected_at) of a graded verdict class counts; a
        # rehearsed-but-never-selected bet was never bet on, so it has no outcome.
        if verdict not in _GRADED_VERDICTS or not p.get("selected_at"):
            continue
        score = _bet_score(p)
        if score is None:
            continue  # outcome unknown -> honest abstention, excluded
        buckets.setdefault(verdict, []).append((score, _shipped(p)))
    out = {}
    for verdict, rows in buckets.items():
        n = len(rows)
        if n < CALIBRATION_MIN_N:
            continue  # n-floor in code: a too-thin class is omitted, not caveated
        shipped_n = sum(1 for _, s in rows if s)
        out[verdict] = {
            "n": n,
            "shipped": shipped_n,
            "rate": round(shipped_n / n, 2),
            "score": round(sum(s for s, _ in rows) / n, 2),
        }
    return out


def format_phrase(calibration, verdict):
    """A terse, plain-text accountability line for the given verdict class, or "" when
    the class is below the n-floor (absent from `calibration`). Argo voice: no markdown,
    no em dashes. Example: "My SHIP calls have shipped 4 of 7 so far.\""""
    c = (calibration or {}).get(verdict)
    if not c:
        return ""
    return f"My {verdict} calls have shipped {c['shipped']} of {c['n']} so far."
