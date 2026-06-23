"""Argo's proactive pulse: the ONE owner-facing nudge that wasn't already covered.

Argo already pings you when a prediction resolves (argo_predictions.score_due,
notify=_send) and when a self-fix ships and holds (argo_diagnose.confirm_deployed),
and it flags severe failures in real time (argo_incidents critical alerts). Adding
those to a "pulse" would just double-notify. The one gap left: a project you rated
highly and then never returned to. A real cofounder resurfaces that -- once, gently --
instead of letting it die silently.

Deliberately DETERMINISTIC and hard-gated, NOT a model call: a project rated >=
ENERGY_BAR whose last touch (rated_at / shown_at) is >= STALE_DAYS old gets ONE nudge,
ever (deduped by id), and at most one pulse per week total. Those three gates ARE the
anti-spam bar -- a forgotten >=8/10 bet is unambiguous, so no judge is needed.

Placement triad: registered as the 'pulse' LOCAL_COMMAND, run by argo_scheduled.
local_loop on the Railway volume where PROJECTS_LOG and the _pulse_meta both live and
are consumed. On an Actions checkout the project log is empty, so it is a structural
no-op there -- by design. Stdlib + the shared-utils layer only.
"""

from datetime import datetime, timezone

import argo_incidents
import argo_paths
import argo_store
import send_telegram
from argo_log import get_logger

log = get_logger(__name__)

# Module-level so tests can patch it (mock.patch.object(argo_pulse, "PROJECTS_LOG",
# tmp)); helpers read this global at call time so the override bites.
PROJECTS_LOG = argo_paths.PROJECTS_LOG

ENERGY_BAR = 8           # only resurface projects you rated this highly
STALE_DAYS = 10          # ...and haven't touched in this many days
PULSE_COOLDOWN_DAYS = 7  # at most one pulse a week, total (the cadence cap)
SEEN_CAP = 200           # cap the deduped-id list so the meta stays small
_PULSE_META_KEY = "_pulse_meta"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(s):
    """Parse the timestamp shapes Argo writes onto a project: rated_at
    ('%Y-%m-%d %H:%M UTC'), shown_at (ISO with microseconds + Z), or a bare date.
    Returns an aware datetime, or None if unparseable."""
    if not s:
        return None
    s = str(s).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # shown_at: '...T..:..:...%fZ' (fromisoformat handles microseconds + offset)
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _last_touch(p):
    """Most recent moment the owner engaged this project: max(rated_at, shown_at).
    None if neither parses (then it can't be judged stale)."""
    stamps = [t for t in (_parse_ts(p.get("rated_at")), _parse_ts(p.get("shown_at"))) if t]
    return max(stamps) if stamps else None


def _days_since(dt):
    return None if dt is None else (_now() - dt).days


def _stale_candidates(seen):
    """High-energy projects gone cold and not yet nudged, stalest first.
    Returns a list of (age_days, project_id, energy)."""
    projects = argo_store.load_json(PROJECTS_LOG, [])
    if not isinstance(projects, list):
        return []
    out = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        if not pid or f"proj:{pid}" in seen:
            continue
        try:
            energy = int(p.get("energy") or 0)
        except (TypeError, ValueError):
            continue
        if energy < ENERGY_BAR:
            continue
        age = _days_since(_last_touch(p))
        if age is None or age < STALE_DAYS:
            continue
        out.append((age, pid, energy))
    out.sort(reverse=True)  # stalest (largest age) first
    return out


def _nudge_text(energy, age):
    """Plain text, Argo's voice (no markdown, no em dashes), cited like a human."""
    return (f"you rated a project {energy}/10 about {age} days back and we haven't "
            "moved on it since. want to pick it up, or should i let it go? say 'show "
            "me the project' and i'll pull it up.")


def _cooldown_active(meta):
    days = _days_since(_parse_ts(meta.get("last_pulse_at")))
    return days is not None and days < PULSE_COOLDOWN_DAYS


def run_cli():
    """Scheduler entry for the 'pulse' LOCAL_COMMAND. Sends at most one nudge about the
    stalest forgotten high-energy project: weekly-capped and deduped (one nudge per
    project, ever). Best-effort send; never raises out to the scheduler."""
    try:
        meta = argo_incidents.get_meta(_PULSE_META_KEY, {}) or {}
        seen = set(meta.get("seen", []))
        cands = _stale_candidates(seen)
        if not cands:
            return
        if _cooldown_active(meta):
            return  # inside the weekly window; the candidate stays fresh for later
        age, pid, energy = cands[0]
        # Mark seen BEFORE the send so a delivery failure can't re-nudge the same
        # project next tick -- one nudge per project, ever, is the anti-nag guarantee
        # (a lost low-urgency nudge is acceptable; nagging is not).
        meta["seen"] = (list(seen) + [f"proj:{pid}"])[-SEEN_CAP:]
        if send_telegram.try_send_message(_nudge_text(energy, age)):
            meta["last_pulse_at"] = _now().strftime(_TS_FMT)
            log.info("pulse: nudged stale project %s (%d/10, %dd cold)", pid, energy, age)
        argo_incidents.set_meta(_PULSE_META_KEY, meta)
    except Exception:
        log.error("pulse run failed", exc_info=True)


def main():
    run_cli()


if __name__ == "__main__":
    main()
