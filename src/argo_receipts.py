"""Receipts: Argo's graded track record, rendered HONESTLY (PRD F5).

Trust is earned by being provably right, not by features. This renders the two
already-graded stores -- the prediction grader (argo_predictions) and the
per-verdict-class calibration number (argo_calibration) -- as one plain-text
"recent calls + how they graded" summary Argo can cite in conversation.

The whole point is honesty over coverage (CLAUDE.md). Today there are ~0 scored
predictions, so the summary must SAY so ("no graded calls yet") rather than
fabricate a record. Two rails, both inherited from the stores this reads, never
re-implemented here:
  - A graded call is a prediction that is SCORED (scored_at set) and NOT voided,
    with a real True/False outcome. An armed-but-undue or voided prediction is
    not a call that graded, so it is excluded -- the same never-fabricate rule
    the grader (_evaluate) uses.
  - The calibration number(s) come straight from argo_calibration.compute_
    calibration, whose n-floor (CALIBRATION_MIN_N) is enforced IN CODE: a verdict
    class below the floor is simply absent, so this cannot surface a too-thin
    number even by accident. When nothing clears the floor the summary says
    "insufficient data (n<4)" rather than dressing up a thin sample.

Read-only over both stores. Pure-ish: no network, no LLM. Plain text in Argo's
voice -- no markdown, no em dashes (the webhook still runs the result through
_clean_reply as a backstop, like every other surface).
"""

import argo_calibration
import argo_paths
import argo_predictions
import argo_store

# Re-exported so tests patch the module global (mock.patch.object) and a helper
# reads the bare name at call time -- the same patch-the-global contract the rest
# of the codebase uses (argo_self/argo_rehearse). PREDICTIONS_PATH is NOT mirrored
# here: we reuse argo_predictions._load(), which reads its own bare global, so a
# test overrides argo_predictions.PREDICTIONS_PATH directly.
PROJECTS_LOG = argo_paths.PROJECTS_LOG

CALIBRATION_MIN_N = argo_calibration.CALIBRATION_MIN_N  # the n-floor, surfaced for the honest line

# How many recent graded calls to list. Coarse on purpose: the receipt is a
# trust signal, not a ledger dump.
RECENT_N = 5


def _graded_calls():
    """The scored, non-voided predictions -- the calls that actually graded, ordered
    OLDEST-GRADED to NEWEST-GRADED by scored_at (so the recent-N slice is genuinely
    the most recently graded, not merely the most recently created -- a prediction
    arms and scores on a different clock than it was recorded on). A voided prediction
    (a retracted premise) and an armed-but-undue one are NOT graded calls, so they are
    excluded; never counted as a record."""
    items = argo_predictions._load()
    calls = [p for p in items
             if isinstance(p, dict) and p.get("scored_at") and not p.get("voided")
             and p.get("correct") is not None]
    return sorted(calls, key=lambda p: p.get("scored_at") or "")


def _call_line(pred):
    """One plain-text receipt line for a graded call: what was predicted and how
    reality graded it. The claim is flattened to a single line and any leading bullet
    or header marker is stripped (an EVOLVE-authored claim can carry newlines or begin
    "- "/"# ", which _clean_reply would otherwise eat off the rendered line) -- no
    markdown, no em dashes."""
    claim = " ".join((pred.get("claim") or "").split())
    claim = claim.lstrip("-*+# ") or "(no claim recorded)"
    held = bool(pred.get("correct"))
    outcome = "it held (correct)" if held else "it did not hold (incorrect)"
    return f"{claim} ... {outcome}"


def _calibration_block(projects):
    """The per-verdict-class calibration phrase(s) that clear the n-floor, or an
    honest insufficient-data line. Reuses argo_calibration entirely (compute +
    format_phrase) -- the n-floor lives there, not here."""
    cal = argo_calibration.compute_calibration(projects)
    phrases = [argo_calibration.format_phrase(cal, v) for v in cal]
    phrases = [p for p in phrases if p]
    if phrases:
        return "How my build calls have graded so far:\n" + "\n".join(phrases)
    return ("Build-call calibration: insufficient data (n<%d committed, graded "
            "bets per verdict class), so I'm not putting a number on it yet."
            % CALIBRATION_MIN_N)


def track_record(recent_n=RECENT_N):
    """The full receipts summary as plain text, rendered honestly when data is
    sparse. Read-only over the predictions store and the project log.

    - zero graded calls  -> "No graded calls yet ..." (never a fabricated record)
    - some graded calls  -> the most recent N, each with how reality graded it
    - calibration        -> the verdict classes at/above the n-floor, else an
                            explicit insufficient-data line
    """
    calls = _graded_calls()
    projects = argo_store.load_json(PROJECTS_LOG, [])
    if not isinstance(projects, list):
        projects = []

    parts = []
    if not calls:
        parts.append("No graded calls yet. I've not had a prediction come due and "
                     "score, so there's no track record to show you yet, I'd rather "
                     "say that than dress one up.")
    else:
        recent = calls[-recent_n:]
        correct = sum(1 for p in recent if p.get("correct"))
        header = (f"My most recent {len(recent)} graded calls "
                  f"({correct} of {len(recent)} held):")
        parts.append(header)
        parts.extend(_call_line(p) for p in recent)

    parts.append(_calibration_block(projects))
    return "\n\n".join(parts)
