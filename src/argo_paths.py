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
# Taste signals, learned interactively from screenshots/urls the user sends. Same
# reason: the webhook writes these at chat time, so point ARGO_TASTE_PATH at the
# Railway volume or each redeploy wipes the store (IDs reset to T-001).
TASTE_PATH = Path(os.environ.get("ARGO_TASTE_PATH", str(DATA / "taste_signals.json")))
# Tripwire dedup store. On GitHub Actions it persists by being committed back to
# the repo (see argo-watch.yml / argo-schedule.yml); ARGO_SEEN_PATH lets a runner
# with a writable volume (e.g. Railway) point it there instead, like the stores
# above. argo_watch re-exports this as its module-level SEEN_PATH (the test patch
# point), so load_seen/save_seen read the override at call time.
SEEN_PATH = Path(os.environ.get("ARGO_SEEN_PATH", str(DATA / "argo_seen.json")))
# Self-diagnosis stores: the operational-failure ledger Argo reads back to spot its
# own recurring problems, and the proposal ledger that joins a fix PR to the belief
# it should resolve. Env-overridable for the same reason as the self-model: the live
# bot writes these at chat/scheduler time, so point ARGO_INCIDENTS_PATH /
# ARGO_PROPOSALS_PATH at the Railway volume or each redeploy wipes them. Both are
# gitignored. argo_incidents/argo_diagnose re-export these as module-level constants
# (the test patch points), so their helpers read the override at call time.
INCIDENTS_PATH = Path(os.environ.get("ARGO_INCIDENTS_PATH", str(DATA / "argo_incidents.json")))
PROPOSALS_PATH = Path(os.environ.get("ARGO_PROPOSALS_PATH", str(DATA / "argo_proposals.json")))
