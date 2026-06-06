"""Single source of truth for Argo's filesystem paths.

`ROOT` (`Path(__file__)...parent.parent`) and the named data files (PROJECTS_LOG,
SIGNALS_PATH, SEEN_PATH, ...) used to be re-derived in every module that touched
a store -- PROJECTS_LOG alone was defined in five places. That's five chances to
drift, and no obvious answer to "where does Argo keep its projects?". This module
defines each once.

Home modules still re-export the name they own (e.g. argo_webhook keeps
`PROJECTS_LOG = argo_paths.PROJECTS_LOG`) so the tests that patch a module-level
constant -- `mock.patch.object(wh, "PROJECTS_LOG", tmp)` -- keep working: the
helper reads the unqualified module global at call time, so the patched value is
what it sees. Never read `argo_paths.PROJECTS_LOG` directly inside a helper, or
that override stops biting.

CHAT_LOG_PATH and PROFILE_PATH keep their env overrides (ARGO_CHAT_LOG /
ARGO_PROFILE_PATH) -- the live Railway deploy points the chat log at a mounted
volume, and tests point the profile at a temp file. Stdlib only.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PROJECTS_LOG = DATA / "argo_projects.json"
SIGNALS_PATH = DATA / "signals.json"
SEEN_PATH = DATA / "argo_seen.json"
SCHEDULE_PATH = DATA / "schedule.json"
STATE_PATH = DATA / "schedule_state.json"
FINDINGS_DIR = ROOT / "findings"

# Env-overridable: the live bot mounts the chat log on a Railway volume so it
# survives redeploys; tests point the profile at a temp file.
CHAT_LOG_PATH = Path(os.environ.get("ARGO_CHAT_LOG", str(DATA / "argo_chat.json")))
PROFILE_PATH = Path(os.environ.get("ARGO_PROFILE_PATH", str(DATA / "profile.json")))
# Argo's self-model. Env-overridable for the same reason as the chat log: point
# ARGO_SELF_PATH at the Railway volume so self-beliefs survive redeploys.
SELF_PATH = Path(os.environ.get("ARGO_SELF_PATH", str(DATA / "argo_self.json")))
