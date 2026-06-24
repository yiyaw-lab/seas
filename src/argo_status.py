"""
Argo — ambient status (H3.3): "what's in flight / needs attention".

A READ-ONLY query over the stores Argo already keeps -- predictions, the
evolution lever ledger, the diagnostic fix-proposal ledger, pending
decisions/heals, and the world model -- assembled into one "what needs my
attention right now" view, plus a who-acts-next classifier so the answer is
actionable rather than a flat list.

Why it exists: Argo proposed this itself (build-log 2026-06-05) as a UX
evolution -- surface in-flight work on demand instead of weekly drops. It is
largely a query over stores that already exist, not new infrastructure.

The who-acts-next classifier (Finding_036) tags each in-flight item:

  needs-you      the human must act before this advances
                 -- a staged EVOLVE/SKIP or FIX/IGNORE gate, a PR awaiting your
                    merge, an open question Argo asked you.
  agent-can-act  Argo's own loops will advance it unattended
                 -- a due prediction the grader will score, a lever mid-rehearse
                    or in its post-merge deploy watch, a merged fix awaiting the
                    confirm loop.
  blocked        cannot advance without an external fix
                 -- a fix PR whose CI failed, an evolution lever whose authoring
                    failed, a prediction whose clock cannot start (unarmed: its
                    merge has not landed).

Without that tag "needs attention" is unanswerable -- a due prediction and a
staged PR are both "in flight" but only one is waiting on you.

This module NEVER mutates any store: it loads via the shared readers
(world_model.get_beliefs, argo_predictions._load) or argo_store.load_json over
the canonical argo_paths constants, and reports. It is deliberately import-light
(no MCP/flask) so the webhook gate and the unit tests can import it cheaply;
the pending decisions/heal stores are read directly as JSON (the same atomic
store layer) rather than importing argo_mcp_server just to read two files.

Surfaced as the webhook STATUS command (argo_webhook), upstream of the model,
plain text only (no markdown, no em dashes), so the answer is deterministic and
does not depend on the LLM choosing a tool.
"""

from datetime import datetime, timezone

import argo_incidents
import argo_paths
import argo_store
import world_model
import argo_predictions

# Verdicts (who-acts-next). Kept as plain strings -- the rule set below is the
# single source of truth for which verdict each in-flight item gets.
NEEDS_YOU = "needs-you"
AGENT_CAN_ACT = "agent-can-act"
BLOCKED = "blocked"

# How many recent belief moves to surface as context (informational, unclassified
# -- a belief move is a report of PAST movement, not in-flight work awaiting a
# next actor).
RECENT_BELIEF_LIMIT = 5

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(ts):
    """Parse a Zulu timestamp; None on anything unparseable (never raises)."""
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --- in-flight collectors (each returns a list of item dicts) -----------------
#
# An item dict is: {"verdict", "kind", "id", "summary"} -- verdict drives the
# grouping, kind/id/summary the rendered line. Collectors are read-only and
# defensive: a missing or malformed store yields [] (load_json returns the
# default), never an exception.


def _predictions_in_flight():
    """Armed-but-unscored predictions. Due ones the grader will score next run
    (agent-can-act); unarmed ones whose clock cannot start until their merge
    lands are waiting on an upstream event (blocked)."""
    out = []
    now = _now()
    for p in argo_predictions._load():
        if p.get("scored_at") or p.get("voided"):
            continue  # settled (scored or voided): no longer in flight
        pid = p.get("id", "?")
        claim = (p.get("claim") or "").strip()
        if not p.get("armed_at"):
            out.append({
                "verdict": BLOCKED, "kind": "prediction", "id": pid,
                "summary": f"{pid} not armed yet, waiting on its change to land: {claim}",
            })
            continue
        due = _parse_ts(p.get("due"))
        if due is not None and due <= now:
            out.append({
                "verdict": AGENT_CAN_ACT, "kind": "prediction", "id": pid,
                "summary": f"{pid} is due, the grader will score it next run: {claim}",
            })
        else:
            when = p.get("due") or "?"
            out.append({
                "verdict": AGENT_CAN_ACT, "kind": "prediction", "id": pid,
                "summary": f"{pid} armed, scores on {when}: {claim}",
            })
    return out


def _evolution_in_flight():
    """In-flight evolution levers from the ledger. pr_open awaits your merge
    (needs-you); evolving/accepted/merged_watch are Argo's loops in progress
    (agent-can-act); failed authoring is blocked. nudge-ready/nudged levers
    surface via the staged-gate collector below, not here."""
    out = []
    try:
        import argo_evolve
        levers = argo_evolve._load_ledger().get("levers", [])
    except Exception:
        return out
    for l in levers:
        status = l.get("status")
        lid = l.get("id", "?")
        feature = l.get("feature") or l.get("lever") or "an upgrade"
        if status == "pr_open":
            out.append({
                "verdict": NEEDS_YOU, "kind": "evolution", "id": lid,
                "summary": f"{lid} ({feature}) has a PR open, waiting on your merge",
            })
        elif status in ("evolving", "accepted"):
            out.append({
                "verdict": AGENT_CAN_ACT, "kind": "evolution", "id": lid,
                "summary": f"{lid} ({feature}) is being rehearsed and drafted",
            })
        elif status == "merged_watch":
            out.append({
                "verdict": AGENT_CAN_ACT, "kind": "evolution", "id": lid,
                "summary": f"{lid} ({feature}) merged, in its post-deploy watch",
            })
        elif status == "failed":
            out.append({
                "verdict": BLOCKED, "kind": "evolution", "id": lid,
                "summary": f"{lid} ({feature}) failed authoring or CI, resting before retry",
            })
    return out


def _proposals_in_flight():
    """In-flight diagnostic fix PRs from the proposal ledger. CI failed -> blocked;
    merged-but-not-resolved -> the confirm loop will close it (agent-can-act);
    open and not failed -> awaiting your merge (needs-you).

    Skips evolution-origin rows (incident_key falsy): argo_evolve's _ensure_proposal_row
    records every adopted EVOLVE PR in this same ledger with incident_key=None, so
    without this skip a single upgrade PR would surface TWICE -- once correctly as an
    evolution lever (pr_open) and once here mislabelled as a "fix PR for an incident."
    Real diagnostic self-fixes always carry a truthy incident_key (the incident cluster
    key), so this never drops a genuine fix PR."""
    proposals = argo_store.load_json(argo_paths.PROPOSALS_PATH, [])
    if not isinstance(proposals, list):
        return []
    out = []
    for p in proposals:
        if p.get("resolved"):
            continue  # terminal
        if not p.get("incident_key"):
            continue  # evolution-origin PR -- surfaced by _evolution_in_flight, not here
        num = p.get("pr_number", "?")
        # incident_key embeds the raw fingerprint (emails/tokens survive _fingerprint),
        # and this summary is sent to the user -- so scrub it before it is rendered.
        key = argo_incidents._redact(p.get("incident_key") or "an incident")
        if p.get("ci_failed"):
            out.append({
                "verdict": BLOCKED, "kind": "fix-pr", "id": f"PR-{num}",
                "summary": f"fix PR #{num} for {key} failed CI",
            })
        elif p.get("merged"):
            out.append({
                "verdict": AGENT_CAN_ACT, "kind": "fix-pr", "id": f"PR-{num}",
                "summary": f"fix PR #{num} for {key} is merged, in its deploy watch",
            })
        else:
            out.append({
                "verdict": NEEDS_YOU, "kind": "fix-pr", "id": f"PR-{num}",
                "summary": f"fix PR #{num} for {key} is open, waiting on your merge",
            })
    return out


def _staged_gates_in_flight():
    """Single-slot staged gates that wait on an explicit human reply: an EVOLVE/SKIP
    upgrade and a FIX/IGNORE self-fix (plus the safe heals and open decisions).
    All needs-you by definition -- they are blocked on your one-word answer. Read
    the JSON directly via the shared store layer to stay import-light (no MCP/flask)."""
    out = []

    # EVOLVE / SKIP: a frontier upgrade staged behind the gate.
    pend = argo_store.load_json(argo_paths.PENDING_EVOLVE_PATH, None)
    if isinstance(pend, dict) and pend.get("lever_id"):
        out.append({
            "verdict": NEEDS_YOU, "kind": "gate", "id": pend["lever_id"],
            "summary": f"upgrade {pend['lever_id']} is staged, reply EVOLVE or SKIP",
        })

    # FIX / IGNORE (or a safe heal): a staged heal action awaiting confirmation.
    heal = argo_store.load_json(argo_paths.PENDING_HEAL_PATH, None)
    if isinstance(heal, dict) and heal.get("action"):
        action = heal["action"]
        if action == "propose_fix":
            payload = heal.get("payload") or {}
            title = payload.get("title") or payload.get("incident_key") or "a self-fix"
            out.append({
                "verdict": NEEDS_YOU, "kind": "gate", "id": "heal",
                "summary": f"a self-fix is staged ({title}), reply FIX or IGNORE",
            })
        else:
            out.append({
                "verdict": NEEDS_YOU, "kind": "gate", "id": "heal",
                "summary": f"a {action} heal is staged, reply CONFIRM or CANCEL",
            })

    # Open decisions Argo asked you (ask_owner): each awaits your reply.
    decisions = argo_store.load_json(argo_paths.PENDING_DECISIONS_PATH, [])
    if isinstance(decisions, list):
        for d in decisions:
            if d.get("status") == "open":
                did = d.get("id", "?")
                q = (d.get("question") or "").strip()
                out.append({
                    "verdict": NEEDS_YOU, "kind": "decision", "id": did,
                    "summary": f"{did} is waiting on your answer: {q}",
                })
    return out


def _recent_belief_moves(limit=RECENT_BELIEF_LIMIT):
    """The most recently-updated world-model beliefs, as informational context
    (not in-flight work, so unclassified). Sorted by last_updated, newest first."""
    beliefs = world_model.get_beliefs()
    if not isinstance(beliefs, list):
        return []
    rated = sorted(beliefs, key=lambda b: b.get("last_updated") or "", reverse=True)
    out = []
    for b in rated[:limit]:
        conf = b.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
        claim = (b.get("claim") or "").strip()
        out.append(
            f"{b.get('id', '?')} ({b.get('status', '?')}, conf {conf_s}, "
            f"updated {b.get('last_updated', '?')}): {claim}"
        )
    return out


def collect():
    """Assemble the full ambient-status view. Returns a dict:
        {"items": [classified in-flight item dicts], "beliefs": [context strings]}
    Read-only over every store. Never raises on a missing/empty/malformed store."""
    items = []
    items.extend(_predictions_in_flight())
    items.extend(_evolution_in_flight())
    items.extend(_proposals_in_flight())
    items.extend(_staged_gates_in_flight())
    return {"items": items, "beliefs": _recent_belief_moves()}


# --- rendering ----------------------------------------------------------------

# Group headers in priority order: what needs you first, then what Argo is
# handling, then what is stuck.
_GROUPS = (
    (NEEDS_YOU, "Needs you"),
    (AGENT_CAN_ACT, "I'm on these"),
    (BLOCKED, "Blocked"),
)


def render(status=None):
    """Render the ambient status as plain text (no markdown, no em dashes,
    Telegram-friendly). Graceful when nothing is in flight."""
    if status is None:
        status = collect()
    items = status.get("items", [])
    beliefs = status.get("beliefs", [])

    if not items and not beliefs:
        return "Nothing in flight right now, and no belief moves to report. All quiet."
    if not items:
        lines = ["Nothing in flight right now, all quiet."]
        lines.append("")
        lines.append("Recent belief moves:")
        lines.extend(beliefs)
        return "\n".join(lines)

    lines = ["Here's what's in flight:"]
    for verdict, header in _GROUPS:
        group = [it for it in items if it.get("verdict") == verdict]
        if not group:
            continue
        lines.append("")
        lines.append(f"{header}:")
        for it in group:
            # No leading "- " bullet: that is markdown _clean_reply strips, which
            # would leave a dangling space. Plain lines by construction.
            lines.append(it["summary"])

    if beliefs:
        lines.append("")
        lines.append("Recent belief moves:")
        lines.extend(beliefs)
    return "\n".join(lines)
