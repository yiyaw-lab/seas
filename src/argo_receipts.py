"""Receipts -- Argo's graded track record, rendered for the pull surface (F5).

Trust is earned by being provably right, not by features. This module surfaces the
two stores that already grade Argo's own calls so it can cite them in conversation:

  - the prediction grader (argo_predictions): dated, machine-scored predictions tied to
    world-model beliefs. A scored prediction either HELD or did not -- that is the
    "recent calls + how they graded" the PRD asks for.
  - the build-decision calibration (argo_calibration over the project log): "when Argo
    says SHIP, it ships N of M" -- the world-model bets the operator committed and graded.

Honesty rails are inherited, not re-rolled: voided predictions are excluded (they were
retracted, never graded), an unscored/pending prediction is not counted as a miss, and
the calibration number stays below its n-floor un-surfaceable (compute_calibration omits
a too-thin class entirely). When there is no graded track record yet, render an honest
empty state -- never a fabricated number.

Pure read + render. Reuses argo_predictions._load (scored predictions), argo_calibration
.compute_calibration (the bet curve), and the shared store/log layer. No new store, no
new path constant -- it reads the volume stores the grader already writes, and is wired
into the SAME webhook process (the pull command), so the placement triad holds: it reads
where the grader wrote, in the process the user is talking to.
"""

import argo_calibration
import argo_paths
import argo_predictions
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported so tests patch the module global (mock.patch.object); the renderer reads
# the bare name at call time so the override bites (mirrors argo_predictions/argo_paths).
PROJECTS_LOG = argo_paths.PROJECTS_LOG

# How many recently-scored predictions to cite by name in the summary. A receipt is a
# citation, not a dump: a handful of named calls reads like a peer, a wall does not.
RECENT_CALLS = 3


def _scored_predictions():
    """Predictions that were actually graded -- scored AND not voided. A voided
    prediction was retracted before reality judged it, so it is no track record at all
    and must never count as a hit or a miss (same never-fabricate rule the grader uses).
    Most-recently-scored last (the store appends; scored_at is the grade time)."""
    items = argo_predictions._load()
    graded = [p for p in items
              if p.get("scored_at") and not p.get("voided") and p.get("correct") is not None]
    graded.sort(key=lambda p: p.get("scored_at") or "")
    return graded


def render_receipts():
    """A terse, plain-text track-record summary Argo can cite in chat, or an honest
    empty state when nothing has graded yet. Argo voice: no markdown, no em dashes, cite
    like a human. Reads the prediction store + the project-log calibration only."""
    graded = _scored_predictions()
    held = [p for p in graded if p.get("correct")]
    missed = [p for p in graded if not p.get("correct")]

    projects = argo_store.load_json(PROJECTS_LOG, [])
    if not isinstance(projects, list):
        projects = []
    calibration = argo_calibration.compute_calibration(projects)

    # Nothing graded on either axis yet: be honest, do not invent a number.
    if not graded and not calibration:
        return ("No track record to show yet. I haven't had a prediction or a committed "
                "bet of mine grade out so far. Once my calls start resolving, this is "
                "where I'll show you how they landed.")

    lines = []

    if graded:
        lines.append(f"My predictions: {len(held)} held, {len(missed)} didn't, "
                     f"out of {len(graded)} that have graded so far.")
        # Cite the most recent few by claim + how each landed -- the human citation.
        for p in reversed(graded[-RECENT_CALLS:]):
            claim = (p.get("claim") or "").strip() or "(an unnamed call)"
            word = "held" if p.get("correct") else "didn't hold"
            lines.append(f"- {claim}: {word}.")

    # The build-decision calibration: only classes above the n-floor are present.
    for verdict in ("SHIP", "REVISE"):
        phrase = argo_calibration.format_phrase(calibration, verdict)
        if phrase:
            lines.append(phrase)

    return "\n".join(lines)
