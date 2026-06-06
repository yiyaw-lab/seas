"""Tiny JSON read/write for Argo's stores -- one place for a pattern that was
copy-pasted 50+ times.

Every store (projects, seen-items, scheduler state, taste, ...) read with the
same `json.loads(path.read_text())` wrapped in `except (json.JSONDecodeError,
ValueError): <default>`, and wrote with the same `path.write_text(json.dumps(...,
indent=2) + "\\n")`. The duplication wasn't dangerous, just noise -- and a place
for the corrupt-file handling to silently differ between modules.

`save_json` MUST stay byte-identical to that format (indent=2, trailing newline,
no sort_keys): the seen-store and scheduler tests round-trip files on disk, and
a different format would either break them or churn every data file. `load_json`
returns `default` on a missing file OR unreadable JSON, matching what every call
site did by hand. Callers pass their own default ({}, [], {"schedules": []}).

Stdlib only.
"""

import json


def load_json(path, default=None):
    """Read JSON from `path`; return `default` if the file is missing or its
    contents aren't valid JSON. Mirrors the hand-rolled load each store used."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return default


def save_json(path, data):
    """Write `data` as pretty JSON with a trailing newline -- the exact on-disk
    format every Argo store already uses (indent=2, no sort_keys)."""
    path.write_text(json.dumps(data, indent=2) + "\n")
