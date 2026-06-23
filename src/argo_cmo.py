"""CMO role-lens state -- the cheapest demand test for playbook bet B-007.

B-007 is the expensive bet "a multi-role exec team with real data pipelines".
This module is the CHEAP demand test for it: a per-chat SYSTEM-PROMPT lens (Argo
reasons as the builder's CMO), NOT a data pipeline. The signal we want to grade
later is settling: does the builder return to CMO mode unprompted, post-novelty,
on a real decision? To make that gradable we record a TIMESTAMPED switch log per
chat -- every on/off flip with a UTC iso8601 stamp -- so a manual grade can read
the cadence of returns straight out of the store.

State shape, keyed by str(chat_id):
  {str(chat_id): {"active": bool,
                  "switches": [{"ts": "<iso8601>", "to": "on"|"off"}, ...]}}

Backed by the volume-capable ARGO_CMO_MODES_PATH (see argo_paths). Reads are
robust to a missing/corrupt store (default off). Stdlib + the shared argo_store
I/O and argo_log only.
"""

from datetime import datetime, timezone

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Module-level so tests can patch it (mock.patch.object(argo_cmo, "CMO_MODES_PATH",
# tmp)); is_active/set_active read this global at call time so the override bites.
CMO_MODES_PATH = argo_paths.CMO_MODES_PATH

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now_iso():
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _load():
    """The whole store as a dict, or {} on a missing/corrupt/wrong-shape store --
    a bad store must never crash a chat turn, it just reads as 'lens off'."""
    data = argo_store.load_json(CMO_MODES_PATH, {})
    return data if isinstance(data, dict) else {}


def is_active(chat_id):
    """True if the CMO lens is on for this chat. Defaults to False for an unknown
    chat or an unreadable store. chat_id is keyed as a string."""
    if chat_id is None:
        return False
    entry = _load().get(str(chat_id))
    return bool(entry.get("active")) if isinstance(entry, dict) else False


def set_active(chat_id, on):
    """Turn the lens on/off for this chat and append a timestamped switch row, so
    the B-007 return-cadence is gradable later. Idempotent on the flag but ALWAYS
    logs the switch event (an explicit /cmo on while already on is still a signal
    the builder reached for the lens). Returns the new bool state."""
    if chat_id is None:
        return bool(on)
    key = str(chat_id)
    data = _load()
    entry = data.get(key)
    if not isinstance(entry, dict):
        entry = {"active": False, "switches": []}
    if not isinstance(entry.get("switches"), list):
        entry["switches"] = []
    entry["active"] = bool(on)
    to = "on" if on else "off"
    entry["switches"].append({"ts": _now_iso(), "to": to})
    data[key] = entry
    CMO_MODES_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(CMO_MODES_PATH, data)
    log.info("cmo lens switch chat=%s to=%s", key, to)
    return bool(on)
