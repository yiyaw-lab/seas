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

# Env-overridable like the chat/self/taste stores: the live bot appends a new
# project here every time it proposes one, so point ARGO_PROJECTS_PATH at the
# Railway volume or each redeploy wipes the log and the project counter resets to
# P-001 (the "every project is 001" bug). The webhook (interactive proposer) is the
# volume reader/writer; the scheduled "project" job runs on Actions with its own
# separate gitignored copy, so this override only persists the webhook's log (the two
# logs were never shared -- different machines). Home modules re-export this as a
# module-level constant (the test patch point), so their helpers read the override at
# call time -- never read argo_paths.PROJECTS_LOG directly inside a helper.
PROJECTS_LOG = Path(os.environ.get("ARGO_PROJECTS_PATH", str(DATA / "argo_projects.json")))
SIGNALS_PATH = DATA / "signals.json"
SCHEDULE_PATH = DATA / "schedule.json"
STATE_PATH = DATA / "schedule_state.json"
FINDINGS_DIR = ROOT / "findings"

# SEAS pipeline state under data/: opportunities.json + probes.json are regenerated
# each run (no volume override -- rebuilt locally, not persisted like the chat/self
# stores), and argo_bets.json is Argo's V1-era project-bet log.
OPPORTUNITIES_PATH = DATA / "opportunities.json"
PROBES_PATH = DATA / "probes.json"
BETS_PATH = DATA / "argo_bets.json"

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
# High-watermark for the chat-log weakness miner (argo_chatmine): the count of
# chat-log turns already mined, so each daily run only scans turns appended SINCE
# the last run and re-running over the same log records zero new incidents.
# Volume-overridable for the same reason as the chat log itself -- the miner runs in
# the webhook's in-process local_loop on the Railway volume, so its watermark must
# live on the SAME volume the chat log does or every redeploy would re-mine from
# zero and re-inflate counts. Gitignored. argo_chatmine re-exports this as a
# module-level constant (the test patch point).
CHATMINE_WATERMARK_PATH = Path(os.environ.get("ARGO_CHATMINE_WATERMARK_PATH",
                                              str(DATA / "argo_chatmine_watermark.json")))
# Frontier-evolution stores (argo_evolve / argo_predictions): the release-watch
# seen-store, the lever ledger, the single-slot EVOLVE staging file, and the dated
# prediction store. All written at webhook/scheduler time on the live bot, so point
# the ARGO_* overrides at the Railway volume or each redeploy wipes them. All
# gitignored. Home modules re-export these as module-level constants (the test
# patch points), same convention as the stores above.
FRONTIER_SEEN_PATH = Path(os.environ.get("ARGO_FRONTIER_SEEN_PATH",
                                         str(DATA / "argo_frontier_seen.json")))
EVOLUTION_PATH = Path(os.environ.get("ARGO_EVOLUTION_PATH",
                                     str(DATA / "argo_evolution.json")))
PENDING_EVOLVE_PATH = Path(os.environ.get("ARGO_PENDING_EVOLVE_PATH",
                                          str(DATA / "argo_pending_evolve.json")))
# Staged heal action behind the CONFIRM/FIX gate. The diagnostic loop stages it,
# then waits (often hours) for the user's FIX reply -- a window that can span a
# redeploy, so point ARGO_PENDING_HEAL_PATH at the Railway volume or the staged fix
# is silently lost and FIX finds nothing staged. (Sibling of PENDING_EVOLVE_PATH.)
PENDING_HEAL_PATH = Path(os.environ.get("ARGO_PENDING_HEAL_PATH",
                                        str(DATA / "argo_pending_heal.json")))
PREDICTIONS_PATH = Path(os.environ.get("ARGO_PREDICTIONS_PATH",
                                       str(DATA / "argo_predictions.json")))
# Escalation-broker store (F7): pending owner-decisions a credential-less cloud
# caller (e.g. a scheduled /vacation run) brokers through Argo's /mcp. ask_owner
# records one here and Telegrams the question; get_owner_answers matches the
# owner's chat reply back. Written at /mcp-tool time on the live bot, so point
# ARGO_PENDING_DECISIONS_PATH at the Railway volume or each redeploy wipes the
# open decisions (same reason as PENDING_HEAL_PATH). argo_mcp_server re-exports
# this as a module-level constant (the test patch point).
PENDING_DECISIONS_PATH = Path(os.environ.get("ARGO_PENDING_DECISIONS_PATH",
                                             str(DATA / "argo_pending_decisions.json")))
# Acted-on-push instrumentation (argo_pushes): one row per scheduled/unprompted
# "push" Argo sends, marked linked when a user reply lands within the window. The
# proactive senders (project/watch) write it and the webhook links it at chat
# time on the live bot, so point ARGO_PUSHES_PATH at the Railway volume or each
# redeploy wipes the act-on-rate history. Gitignored.
PUSHES_PATH = Path(os.environ.get("ARGO_PUSHES_PATH", str(DATA / "argo_pushes.json")))
# Steerable-proactiveness setting (argo_pushes.should_send, F6): the single
# user-tunable base threshold a push's stakes*confidence must clear to send. The
# webhook writes it when the user runs the PROACTIVE command and the push gate
# reads it on every send, both in-process on the live bot, so point
# ARGO_PROACTIVE_PATH at the Railway volume (mirrors ARGO_PUSHES_PATH) or each
# redeploy resets the user's chosen level. Gitignored. argo_pushes re-exports this
# as a module-level constant (the test patch point), so its helpers read the
# override at call time.
PROACTIVE_PATH = Path(os.environ.get("ARGO_PROACTIVE_PATH", str(DATA / "argo_proactive.json")))
# Files the user sends Argo over Telegram (PDFs, notes, csv, ...). The webhook
# saves each one here at chat time, so point ARGO_FILES_DIR at the Railway
# volume or a redeploy wipes them. Gitignored.
FILES_DIR = Path(os.environ.get("ARGO_FILES_DIR", str(DATA / "files")))
# The webhook's in-process scheduler (argo_scheduled.local_loop) keeps its OWN
# dedupe state, separate from schedule_state.json: the Actions runner commits that
# file into the repo, so sharing it would let an inert Actions fire consume the
# webhook's fire key for the day via the deploy-time checkout.
LOCAL_STATE_PATH = Path(os.environ.get("ARGO_LOCAL_SCHED_STATE",
                                       str(DATA / "schedule_state_local.json")))
