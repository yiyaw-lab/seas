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

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SCHEDULE_PATH = ROOT / "data" / "schedule.json"
STATE_PATH = ROOT / "data" / "schedule_state.json"

# command name -> (module, callable). Importing lazily keeps startup cheap and
# avoids pulling Flask/MCP for a watch-only run.
COMMANDS = {
    "project": ("argo_project", "main"),
    "watch": ("argo_watch", "main"),
}


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _matches(sched, now):
    """True if this schedule should fire at `now` (UTC)."""
    if not sched.get("enabled", True):
        return False
    days = sched.get("days", "daily")
    if days != "daily" and now.weekday() not in _as_list(days):
        return False
    return now.hour in _as_list(sched.get("hour", []))


def _load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return default


def _fire_key(sched, now):
    """Dedupe key: one fire per schedule per UTC hour."""
    return f"{sched.get('name', sched.get('command'))}@{now:%Y-%m-%dT%H}"


def run_command(command):
    mod_name, fn_name = COMMANDS[command]
    mod = __import__(mod_name)
    getattr(mod, fn_name)()


def main():
    dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    config = _load(SCHEDULE_PATH, {"schedules": []})
    state = _load(STATE_PATH, {"fired": []})
    fired = set(state.get("fired", []))

    due = []
    for sched in config.get("schedules", []):
        cmd = sched.get("command")
        if cmd not in COMMANDS:
            continue
        if _matches(sched, now) and _fire_key(sched, now) not in fired:
            due.append(sched)

    print(f"\n⏰ Argo schedule runner — {now:%Y-%m-%d %H:%M UTC}")
    if not due:
        print("Nothing due this hour.\n")
        return

    for sched in due:
        key = _fire_key(sched, now)
        print(f"  -> {sched.get('name')} [{sched['command']}]"
              + (" (dry-run)" if dry else ""))
        if dry:
            continue
        try:
            run_command(sched["command"])
            fired.add(key)
        except Exception as exc:
            print(f"     failed: {type(exc).__name__}: {exc}")

    if not dry:
        # keep the dedupe file small: only retain today's keys
        today = f"{now:%Y-%m-%dT}"
        state["fired"] = [k for k in fired if today in k]
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    print("\n✅ Schedule run complete.\n")


if __name__ == "__main__":
    main()
