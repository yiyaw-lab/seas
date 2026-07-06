"""Compact tripwire watch-run ledger.

Placement triad: trigger = argo_watch.main from the scheduled local_loop on the
Railway app; filesystem = WATCH_RUNS_PATH, normally a Railway volume path;
consumer = the same app's health JSON and MCP get_watch_status tool. Manual CLI
runs can write their local checkout copy, but the live status source is the
volume-bound ledger.
"""

from datetime import datetime, timezone

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

WATCH_RUNS_PATH = argo_paths.WATCH_RUNS_PATH
RUN_LEDGER_CAP = 200
MAX_DETAIL_ITEMS = 5


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one_line(text):
    return " ".join(str(text).split())[:240]


def new_run(no_send=False):
    return {
        "started_at": _now_iso(),
        "finished_at": None,
        "dry_run": bool(no_send),
        "candidates": 0,
        "rss_candidates": 0,
        "grok_candidates": 0,
        "judge_kept": 0,
        "suppressed": 0,
        "sent": 0,
        "seen_store_written": False,
        "suppression_reasons": [],
        "errors": [],
    }


def add_suppression(run, reason):
    reasons = run.setdefault("suppression_reasons", [])
    if len(reasons) < MAX_DETAIL_ITEMS:
        reasons.append(_one_line(reason))


def add_error(run, message):
    errors = run.setdefault("errors", [])
    if len(errors) < MAX_DETAIL_ITEMS:
        errors.append(_one_line(message))


def append(run):
    """Append one run row. Never blocks watch delivery on ledger write failure."""
    try:
        WATCH_RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = argo_store.load_json(WATCH_RUNS_PATH, []) or []
        if not isinstance(data, list):
            data = []
        data.append(dict(run))
        argo_store.save_json(WATCH_RUNS_PATH, data[-RUN_LEDGER_CAP:])
    except (OSError, TypeError, ValueError):
        log.warning("could not append watch run ledger", exc_info=True)


def finish(run):
    run["finished_at"] = _now_iso()
    append(run)


def recent(limit=5):
    try:
        n = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        n = 5
    try:
        data = argo_store.load_json(WATCH_RUNS_PATH, []) or []
    except OSError:
        return []
    if not isinstance(data, list):
        return []
    rows = [r for r in data if isinstance(r, dict)]
    return list(reversed(rows[-n:]))


def _yes_no(value):
    return "yes" if value else "no"


def format_status(limit=5):
    rows = recent(limit)
    if not rows:
        return "No watch runs recorded yet."

    lines = []
    for row in rows:
        started = row.get("started_at") or "unknown-time"
        parts = [
            f"{started}: candidates={row.get('candidates', '?')}",
            f"kept={row.get('judge_kept', '?')}",
            f"sent={row.get('sent', '?')}",
            f"suppressed={row.get('suppressed', '?')}",
            f"seen_store={_yes_no(row.get('seen_store_written'))}",
        ]
        if row.get("dry_run"):
            parts.append("dry_run=yes")
        errors = row.get("errors") if isinstance(row.get("errors"), list) else []
        if errors:
            parts.append(f"errors={len(errors)} ({errors[0]})")
        reasons = row.get("suppression_reasons")
        if isinstance(reasons, list) and reasons:
            parts.append(f"last_suppression={reasons[-1]}")
        lines.append(", ".join(parts))
    return "\n".join(lines)
