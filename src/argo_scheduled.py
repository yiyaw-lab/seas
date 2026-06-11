"""
Argo schedule runner — fire deliveries defined in data/schedule.json.

This decouples WHAT runs from WHEN, so new scheduled deliveries are a DATA edit
(data/schedule.json), not a GitHub-workflow edit. That means Argo can propose a
new schedule via its Contents-only PR token (Phase E4) without ever needing the
dangerous Workflows permission.

A single hourly GitHub Actions workflow calls this. For each enabled schedule
whose window matches the current UTC hour, it runs the mapped command. A dedupe
file (data/schedule_state.json) prevents double-firing within the same hour.

Commands map to existing delivery entrypoints:
  "project" -> argo_project.main()   (weekly fresh project to Telegram)
  "watch"   -> argo_watch.main()     (tripwire sweep)

Run:  python3 src/argo_scheduled.py            (fire what's due now)
      python3 src/argo_scheduled.py --dry-run  (print what WOULD fire)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported from argo_paths; kept as module-level names so the scheduler tests
# can patch them (mock.patch.object(argo_scheduled, "SCHEDULE_PATH", tmp)) and
# main() reads the override at call time.
SCHEDULE_PATH = argo_paths.SCHEDULE_PATH
STATE_PATH = argo_paths.STATE_PATH

# command name -> (module, callable). Importing lazily keeps startup cheap and
# avoids pulling Flask/MCP for a watch-only run.
COMMANDS = {
    "project": ("argo_project", "main"),
    "watch": ("argo_watch", "main"),
    "reflect": ("argo_self", "reflect_cli"),
    "diagnose": ("argo_diagnose", "run_cli"),
    "frontier": ("argo_evolve", "run_cli"),
}

# Commands that need the WEBHOOK's filesystem: their ledgers live on the Railway
# volume and their FIX/EVOLVE gates read a staging file the webhook must see. The
# Actions runner can't serve these (its checkout has no incident/evolution state,
# and the webhook can never read what it stages there), so the webhook runs them
# itself via local_loop() in a daemon thread. On Actions they stay structurally
# inert (diagnose: empty ledger; frontier: its own GITHUB_ACTIONS guard).
LOCAL_COMMANDS = ("diagnose", "frontier")
LOCAL_STATE_PATH = argo_paths.LOCAL_STATE_PATH
LOCAL_INTERVAL_SECONDS = 15 * 60


def _as_list(v):
    return v if isinstance(v, list) else [v]


# How many hours late a delayed cron tick may still fire a missed window.
# GitHub's scheduled runs are routinely delayed under load and can land in a
# later UTC hour than the cron requested, which would otherwise skip the window
# entirely. The per-window/day dedupe (see _fire_key) keeps this from re-sending.
GRACE_HOURS = 3


def _due_hour(sched, now):
    """Return the scheduled hour this run should fire for (UTC), or None.

    A window is due if `now` is at or up to GRACE_HOURS after its scheduled
    hour on the same UTC day, so a delayed cron tick still fires it instead of
    skipping it. Returns the *scheduled* hour (not now.hour) so the dedupe key
    is stable across the grace window.
    """
    if not sched.get("enabled", True):
        return None
    days = sched.get("days", "daily")
    if days != "daily" and now.weekday() not in _as_list(days):
        return None
    # Pick the latest scheduled hour we're within grace of (handles back-to-back
    # windows); never fire a future hour.
    candidates = [h for h in _as_list(sched.get("hour", []))
                  if 0 <= now.hour - h <= GRACE_HOURS]
    return max(candidates) if candidates else None


def _fire_key(sched, now, target_hour):
    """Dedupe key: one fire per schedule per scheduled window per UTC day.

    Keyed on the *scheduled* hour, not now's hour, so a window fired late within
    the grace period maps to the same key it would have at its exact hour.
    """
    name = sched.get("name", sched.get("command"))
    return f"{name}@{now:%Y-%m-%d}T{target_hour:02d}"


def run_command(command):
    mod_name, fn_name = COMMANDS[command]
    mod = __import__(mod_name)
    getattr(mod, fn_name)()


def fire_due(only=None, dry=False, state_path=None):
    """One scheduler pass: fire every enabled schedule due this UTC hour, deduped
    per window per day. `only` restricts to a command allowlist (the webhook's
    local loop passes LOCAL_COMMANDS); `state_path` overrides the dedupe store
    (the local loop keeps its own -- see LOCAL_STATE_PATH in argo_paths). Returns
    the list of fired command names."""
    spath = state_path or STATE_PATH
    now = datetime.now(timezone.utc)
    config = argo_store.load_json(SCHEDULE_PATH, {"schedules": []})
    state = argo_store.load_json(spath, {"fired": []})
    fired = set(state.get("fired", []))

    due = []
    for sched in config.get("schedules", []):
        cmd = sched.get("command")
        if cmd not in COMMANDS:
            continue
        if only is not None and cmd not in only:
            continue
        target_hour = _due_hour(sched, now)
        if target_hour is None:
            continue
        key = _fire_key(sched, now, target_hour)
        if key in fired:
            # Record WHY a due window isn't sending, so a "missing delivery" can be
            # told apart from a genuine drop without reverse-engineering it.
            log.info("skipping %s: already fired this window (%s)",
                     sched.get("name"), key)
            continue
        due.append((sched, target_hour))

    print(f"\n⏰ Argo schedule runner — {now:%Y-%m-%d %H:%M UTC}")
    if not due:
        print("Nothing due this hour.\n")
        return []

    ran = []
    for sched, target_hour in due:
        key = _fire_key(sched, now, target_hour)
        print(f"  -> {sched.get('name')} [{sched['command']}]"
              + (" (dry-run)" if dry else ""))
        if dry:
            continue
        log.info("firing %s [%s] target_hour=%02d",
                 sched.get("name"), sched["command"], target_hour)
        try:
            run_command(sched["command"])
            ran.append(sched["command"])
        except Exception as exc:
            # Outermost net: one bad command must not skip the rest. Log with the
            # traceback so the failure is diagnosable, never silently swallowed.
            log.error("schedule command %s failed: %s",
                      sched["command"], exc, exc_info=True)
            try:  # also record it so the diagnostic loop can spot a flapping job
                import argo_incidents
                argo_incidents.record_incident(
                    "scheduler_task_error", f"{sched['command']}: {exc}", str(exc))
            except Exception:
                pass
        finally:
            fired.add(key)

    if not dry:
        # keep the dedupe file small: only retain today's keys
        today = f"{now:%Y-%m-%dT}"
        state["fired"] = [k for k in fired if today in k]
        argo_store.save_json(spath, state)
    print("\n✅ Schedule run complete.\n")
    return ran


def local_loop(only=LOCAL_COMMANDS, interval=LOCAL_INTERVAL_SECONDS):
    """Blocking forever-loop for the webhook's in-process scheduler thread: runs
    only the volume-dependent commands (LOCAL_COMMANDS) against this process's
    filesystem. The per-window dedupe still applies, so polling every 15 minutes
    fires each window once; the grace window covers a slow boot."""
    import time
    log.info("local scheduler: running [%s] every %ds", ", ".join(only), interval)
    while True:
        try:
            fire_due(only=only, state_path=LOCAL_STATE_PATH)
        except Exception:
            log.error("local scheduler pass failed", exc_info=True)
        time.sleep(interval)


def main():
    fire_due(dry="--dry-run" in sys.argv)


if __name__ == "__main__":
    main()
