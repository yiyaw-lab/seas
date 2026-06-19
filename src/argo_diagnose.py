"""Argo's self-diagnosis loop: 'diagnose -> propose -> verify -> confirm'.

This is the half that makes Argo proactive. The observe layer (argo_incidents) turns
failures into a durable ledger; this module reads that ledger on a daily schedule and,
only when a problem actually recurs, spends one cheap guarded model call to name the
likely cause and draft a fix, seeds a self-belief, and texts the owner ONE honest nudge
("I caught N of these, here's my guess and a fix -- reply FIX or IGNORE"). So Argo stops
waiting to be told what's broken.

It also closes the loop on fixes it has already proposed: poll each open PR's CI
(verify_open_proposals), and only after a PR merges AND a quiet post-deploy window passes
with zero recurrence in the ledger does it mark the originating belief resolved
(confirm_deployed). A fix that fails CI or recurs gets refuting evidence and the cluster
reopens -- a confidently-wrong fix can never launder into a false "fixed."

Everything is gated and cheap: four free funnel gates run before any model call, and the
model is the only paid step (routed through argo_observe so the DailyBudget + breaker
apply). Telegram, staging, and CI polling sit behind small seams (_send/_stage_fix/
_check_ci) so the whole loop tests hermetically -- no network, no LLM, no real data files.

Registered as the scheduler's 'diagnose' command (argo_scheduled.COMMANDS). Standard
library + the shared-utils layer.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import argo_incidents
import argo_paths
import argo_self
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported as a module global so tests patch the override (mock.patch.object).
PROPOSALS_PATH = argo_paths.PROPOSALS_PATH
ROOT = argo_paths.ROOT

MIN_COUNT = 3              # a cluster must recur this many times before it escalates
WINDOW_HOURS = 24         # ...within this window (a stale one-off never nudges)
MAX_NUDGES_PER_DAY = 1    # hard ceiling on proactive nudges -- the spam guard
DEPLOY_WATCH_HOURS = 24   # quiet window after merge before a belief may be resolved
PRUNE_DAYS = 14           # drop resolved/muted clusters older than this
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_META_KEY = "_diagnose_meta"

_DIAGNOSE_SYSTEM = ("You are Argo, diagnosing one of your own recurring operational "
                    "failures. Be honest and specific; never guess a fix you are not "
                    "fairly sure of.")
_DIAGNOSE_PROMPT = (
    "A failure has recurred inside your own code. Here is the incident cluster:\n"
    "kind: {kind}\ncount: {count}\nfingerprint: {fingerprint}\n"
    "recent samples:\n{samples}\n\n"
    "Your source files (you may only name files from this list as suspects):\n{files}\n\n"
    "Reply with ONLY a JSON object, no prose, no markdown, with these keys:\n"
    '  "diagnosis": one plain sentence naming the most likely cause.\n'
    '  "suspected_files": a list of 1-3 paths from the list above (e.g. "src/argo_x.py").\n'
    '  "suggestion": one plain sentence describing the concrete fix.\n'
    '  "confident_enough_to_propose": true ONLY if you are fairly sure of both the '
    "cause and a small, testable fix; false if you are guessing.\n"
    "No em dashes. If you cannot point at a real cause, set confident_enough_to_propose "
    "to false and suspected_files to []."
)
# Structured-output schema mirroring the prompt's four keys. Enforced on the Anthropic
# path via output_config so a malformed reply can't silently drop a diagnosis.
# additionalProperties:false + no numeric/length constraints, to satisfy the API.
_DIAGNOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "suspected_files": {"type": "array", "items": {"type": "string"}},
        "suggestion": {"type": "string"},
        "confident_enough_to_propose": {"type": "boolean"},
    },
    "required": ["diagnosis", "suspected_files", "suggestion",
                 "confident_enough_to_propose"],
    "additionalProperties": False,
}


# --- small seams (patched in tests) -----------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().strftime(_TS_FMT)


def _parse_ts(ts):
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _send(text):
    """Best-effort Telegram delivery (non-fatal: try_send_message returns a bool and
    never exits, so a delivery hiccup can't kill the scheduled run)."""
    try:
        import send_telegram
        return send_telegram.try_send_message(text)
    except Exception:
        log.error("diagnose: send failed", exc_info=True)
        return False


def _stage_fix(payload):
    """Stage a propose_fix action for the webhook FIX shortcut. Lazy-imports the MCP
    server (heavy) only when a confident fix is actually ready."""
    import argo_mcp_server
    return argo_mcp_server.stage_fix_proposal(payload)


def _check_ci(pr_number):
    """Read a PR's merge state + CI conclusion via the MCP server's GitHub helper.
    Returns a dict or None (unreadable). Lazy import keeps the no-proposals path light."""
    import argo_mcp_server
    return argo_mcp_server._check_proposal_ci(pr_number)


def _check_reviews(pr_number, seen_ids):
    """Read external review-bot findings on a PR via the MCP server helper, skipping
    comment ids already surfaced. Returns {summary, findings}. Lazy import keeps the
    no-proposals path light."""
    import argo_mcp_server
    return argo_mcp_server._check_proposal_reviews(pr_number, seen_ids)


# --- proposal ledger --------------------------------------------------------

def _load_proposals():
    items = argo_store.load_json(PROPOSALS_PATH, [])
    return items if isinstance(items, list) else []


def _save_proposals(items):
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(PROPOSALS_PATH, items)


def append_proposal(pr_number, url, belief_id, incident_key, head_sha=None):
    """Record a freshly-opened fix PR so verify/confirm can follow it to resolution.
    Called by the MCP server when run_pending_heal opens the PR."""
    items = _load_proposals()
    items.append({
        "pr_number": pr_number, "url": url, "belief_id": belief_id,
        "incident_key": incident_key, "created_at": _now_iso(),
        "ci_conclusion": None, "merged": False, "state": "open", "head_sha": head_sha,
        "merged_at": None, "deploy_watch_until": None, "notified": False,
        "ci_failed": False, "resolved": False, "last_checked": None,
        "seen_review_ids": [],
    })
    _save_proposals(items)
    return items[-1]


# --- the diagnose funnel ----------------------------------------------------

def _repo_files():
    try:
        return sorted("src/" + p.name for p in (ROOT / "src").glob("*.py"))
    except OSError:
        return []


def _resolve_model():
    import argo_observe as observe
    candidates = [(os.environ.get("ARGO_CHAT_MODEL") or "claude-sonnet-4-6")] + observe.resolve_models()
    for m in candidates:
        p = observe.provider_for(m)
        if p and os.environ.get(p["key_env"]):
            return m
    return None


def _parse_json(raw):
    """Pull the first JSON object out of the model reply, tolerantly. None on failure."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def _diagnose_cluster(cluster):
    """One guarded model call: cluster -> {diagnosis, suspected_files, suggestion,
    confident_enough_to_propose} or None. Routes through argo_observe so the daily
    budget + circuit breaker apply; never raises out to the scheduler."""
    import argo_observe as observe
    model = _resolve_model()
    if model is None:
        log.warning("diagnose: no model available")
        return None
    prompt = _DIAGNOSE_PROMPT.format(
        kind=cluster.get("kind"), count=cluster.get("count"),
        fingerprint=cluster.get("fingerprint"),
        samples="\n".join(f"- {s}" for s in cluster.get("samples", [])) or "(none)",
        files="\n".join(_repo_files()) or "(unavailable)")
    try:
        if observe.provider_for(model)["name"] == "anthropic":
            try:
                raw = observe.chat_with_mcp(
                    _DIAGNOSE_SYSTEM, [{"role": "user", "content": prompt}], model,
                    temperature=0.2, output_schema=_DIAGNOSE_SCHEMA)
            except Exception:
                # Structured-output enforcement is best-effort: if the API rejects the
                # schema (unsupported model / shape mismatch), fall back to a plain call
                # so the loop degrades to the tolerant parser instead of going dark.
                log.warning("diagnose: structured-output call failed; plain retry",
                            exc_info=True)
                raw = observe.chat_with_mcp(
                    _DIAGNOSE_SYSTEM, [{"role": "user", "content": prompt}], model,
                    temperature=0.2)
        else:
            raw = observe.generate_observations(prompt, model, temperature=0.2)
    except Exception:
        log.error("diagnose: model call failed", exc_info=True)
        return None
    parsed = _parse_json(raw)
    if parsed is None and (raw or "").strip():
        # A non-empty but unparseable reply means the diagnosis is being dropped on the
        # floor. Record it so this silent parse-failure class becomes measurable -- the
        # hook the structured-output prediction is graded against.
        argo_incidents.record_incident(
            "model_failure", "diagnose json parse failed", sample=(raw or "")[:240])
    return parsed


def _nudge_budget_left():
    meta = argo_incidents.get_meta(_META_KEY, {}) or {}
    today = _now().strftime("%Y-%m-%d")
    if meta.get("last_nudge_date") != today:
        return MAX_NUDGES_PER_DAY
    return max(0, MAX_NUDGES_PER_DAY - int(meta.get("nudges_today", 0)))


def _record_nudge():
    meta = argo_incidents.get_meta(_META_KEY, {}) or {}
    today = _now().strftime("%Y-%m-%d")
    if meta.get("last_nudge_date") != today:
        meta = {"last_nudge_date": today, "nudges_today": 0}
    meta["nudges_today"] = int(meta.get("nudges_today", 0)) + 1
    argo_incidents.set_meta(_META_KEY, meta)


def _already_in_flight(cluster):
    """Gate B: a cluster with a NON-TERMINAL fix PR (awaiting CI or merge) is not
    re-nudged. A proposal that failed CI (ci_failed) or resolved is terminal, so a cluster
    reopened by a CI failure or a post-fix recurrence is free to be re-diagnosed -- which is
    exactly what we want. Keyed on the proposal ledger, not belief status."""
    key = cluster.get("key")
    for p in _load_proposals():
        if (p.get("incident_key") == key and not p.get("resolved")
                and not p.get("ci_failed")):
            return True
    return False


def diagnose():
    """Run the funnel once. Returns a summary dict (never raises out to the scheduler)."""
    # GATE A (structural): only clusters that actually recurred, recently.
    clusters = argo_incidents.open_clusters(min_count=MIN_COUNT, window_hours=WINDOW_HOURS)
    # GATE B (dedup): drop clusters already being worked.
    clusters = [c for c in clusters if not _already_in_flight(c)]
    if not clusters:
        return {"acted": False, "reason": "no eligible clusters"}
    # GATE C (rate-limit): the spam ceiling -- skip entirely (no model call) if spent.
    if _nudge_budget_left() <= 0:
        return {"acted": False, "reason": "daily nudge budget spent"}
    # GATE D (pick one): the single worst, recurrence-after-a-fix weighted up.
    cluster = max(clusters, key=_score)
    key = cluster["key"]

    result = _diagnose_cluster(cluster) or {}
    suspected = [f for f in (result.get("suspected_files") or []) if isinstance(f, str)]
    in_repo = [f for f in suspected if (ROOT / f).exists()]
    confident = (bool(result.get("confident_enough_to_propose"))
                 and bool(in_repo) and len(in_repo) == len(suspected))
    diagnosis = (result.get("diagnosis") or "").strip() or _fallback_claim(cluster)

    recurred = bool(cluster.get("recurred_after_fix"))
    belief_id = _seed_belief(cluster, diagnosis, recurred)

    if not confident:
        # Report-only: a guess never becomes a rigged PR.
        sent = _send(_report_text(cluster, diagnosis))
        if sent:
            _record_nudge()
        argo_incidents.mark(key, status="diagnosed", belief_id=belief_id)
        log.info("diagnose: report-only nudge for %s (belief %s)", key, belief_id)
        return {"acted": True, "confident": False, "key": key, "belief_id": belief_id}

    # Confident: offer a real fix behind the human FIX gate.
    sent = _send(_fix_text(cluster, diagnosis, result.get("suggestion", "")))
    if not sent:
        log.warning("diagnose: fix nudge not delivered for %s; will retry", key)
        return {"acted": False, "reason": "nudge delivery failed", "key": key}
    _record_nudge()
    payload = {
        "title": f"Argo self-fix: {cluster.get('kind')} recurring",
        "description": (f"Diagnosis: {diagnosis}\n\nSuggested fix: "
                        f"{result.get('suggestion', '')}\n\nIncident: {key} "
                        f"(seen {cluster.get('count')}x)."),
        "suspected_files": in_repo,
        "suggestion": result.get("suggestion", ""),
        "belief_id": belief_id,
        "incident_key": key,
        "kind": cluster.get("kind"),
    }
    try:
        _stage_fix(payload)
    except Exception:
        log.error("diagnose: could not stage fix for %s", key, exc_info=True)
    argo_incidents.mark(key, status="diagnosed", belief_id=belief_id)
    log.info("diagnose: staged fix + FIX nudge for %s (belief %s)", key, belief_id)
    return {"acted": True, "confident": True, "key": key, "belief_id": belief_id}


def _score(cluster):
    """Gate D ranking: recurrence weighted by recency, doubled if it came back after a
    fix (that's the most important signal -- a fix that didn't hold)."""
    seen = _parse_ts(cluster.get("last_seen", "")) or _now()
    hours = max(0.0, (_now() - seen).total_seconds() / 3600.0)
    recency = 1.0 / (1.0 + hours / 24.0)
    weight = 2.0 if cluster.get("recurred_after_fix") else 1.0
    return cluster.get("count", 0) * recency * weight


def _fallback_claim(cluster):
    return f"my {cluster.get('kind')} keeps recurring ({cluster.get('fingerprint')})"


def _seed_belief(cluster, diagnosis, recurred):
    """Create or reuse the issue belief. A recurrence after a resolved fix adds refuting
    evidence to the SAME belief (it reopens) instead of seeding a duplicate; a fresh
    cluster seeds a 0.30 belief and earns one piece of supporting evidence (it recurred)."""
    bid = cluster.get("belief_id")
    if recurred and bid:
        argo_self.add_evidence(bid, f"recurred after a fix: {cluster['key']}", supports=False)
        return bid
    bid = argo_self.add_self_belief(diagnosis, kind="issue", source="diagnosis")
    if bid:
        argo_self.add_evidence(
            bid, f"observed {cluster.get('count')}x: {cluster.get('fingerprint')}")
    return bid


def _report_text(cluster, diagnosis):
    return (f"heads up: my {cluster.get('kind')} has happened {cluster.get('count')} "
            f"times lately. my best guess: {diagnosis}. i'm not sure enough of the fix "
            f"to draft one yet, just flagging it.")


def _fix_text(cluster, diagnosis, suggestion):
    return (f"i caught {cluster.get('count')} {cluster.get('kind')} failures. likely "
            f"cause: {diagnosis}. i can draft a fix with a reproduction test for you to "
            f"review: {suggestion}. reply FIX to open the PR, or IGNORE to drop it. i "
            f"can't merge it myself.")


def _sanitize(text):
    """Run external (bot-authored) text through the webhook's canonical plain-text
    sanitizer so a surfaced finding honors Argo's no-markdown / no-em-dash output
    rule. Lazy import avoids a module-load cycle with argo_webhook."""
    import argo_webhook
    return argo_webhook._clean_reply(text or "")


def _first_line(text):
    """First non-empty line of a finding body."""
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _review_text(pr_number, findings):
    """One plain-text Telegram line summarizing new code-review bot findings. Each
    finding head is sanitized + trimmed since it carries raw bot markdown."""
    count = len(findings)
    heads = "; ".join(_sanitize(_first_line(f.get("body")))[:140] for f in findings[:3])
    more = f" (and {count - 3} more)" if count > 3 else ""
    plural = "s" if count != 1 else ""
    return (f"cursorbot reviewed PR #{pr_number} and flagged {count} thing{plural}: "
            f"{heads}{more}. reply and i'll take a pass at addressing them, or tell "
            f"me to leave them.")


# --- verify + confirm (closing the loop on proposed fixes) ------------------

def verify_open_proposals():
    """Poll CI for each open fix PR. Red CI -> refute the belief and reopen the cluster
    (the fix didn't pass). Green + merged -> start the post-deploy watch (do NOT resolve
    yet). Green, not merged -> one 'ready for your merge' nudge. Unreadable -> leave it."""
    items = _load_proposals()
    changed = False
    for p in items:
        # Skip only the genuinely-done: resolved, or merged + in post-deploy watch.
        # A ci_failed proposal is still an OPEN PR on GitHub, so its bot reviews are
        # still surfaced below -- only its (settled) CI verdict is not re-run.
        if p.get("resolved") or p.get("deploy_watch_until"):
            continue
        n = p["pr_number"]
        # CI / merge state machine -- skipped once the fix is parked as ci_failed
        # (re-running it would re-fire the failure nudge on every poll).
        if not p.get("ci_failed"):
            ci = None
            try:
                ci = _check_ci(n)
            except Exception:
                log.error("verify: CI check failed for PR #%s", n, exc_info=True)
            if ci:
                changed = True
                p["last_checked"] = _now_iso()
                p["state"] = ci.get("state", p.get("state"))
                p["ci_conclusion"] = ci.get("ci_conclusion")
                if ci.get("head_sha"):
                    p["head_sha"] = ci["head_sha"]
                concl = ci.get("ci_conclusion")
                if concl in ("failure", "timed_out", "cancelled", "action_required"):
                    argo_self.add_evidence(p["belief_id"], f"PR #{n} CI {concl}", supports=False)
                    argo_incidents.mark(p["incident_key"], status="open")
                    p["ci_failed"] = True
                    _send(f"the fix i proposed (PR #{n}) didn't pass the tests, so i'm not "
                          f"calling it fixed. i've reopened the problem to rethink it.")
                elif ci.get("merged"):
                    p["merged"] = True
                    p["merged_at"] = ci.get("merged_at") or _now_iso()
                    watch_to = (_parse_ts(p["merged_at"]) or _now()) + timedelta(hours=DEPLOY_WATCH_HOURS)
                    p["deploy_watch_until"] = watch_to.strftime(_TS_FMT)
                    _send(f"the fix (PR #{n}) merged. i won't call it fixed until i've watched "
                          f"for the problem coming back over the next day.")
                elif concl == "success" and not p.get("notified"):
                    p["notified"] = True
                    _send(f"CI is green on the fix (PR #{n}). it's ready for your merge whenever "
                          f"you want; i can't merge it myself.")
        # External code review (Cursor Bugbot auto-reviews every open PR): surface NEW
        # findings so the owner sees them without asking and Argo can address them in
        # chat. Runs while the PR is open -- even after a CI failure, since the PR can
        # still gather new findings. Deduped by comment id; best-effort (a failure here
        # must never block the CI loop above).
        try:
            seen = p.get("seen_review_ids") or []
            rev = _check_reviews(n, seen)
            fresh = (rev or {}).get("findings") or []
            if fresh:
                ids = [f["id"] for f in fresh if f.get("id") is not None]
                p["seen_review_ids"] = sorted(set(seen) | set(ids))
                changed = True
                _send(_review_text(n, fresh))
        except Exception:
            log.error("verify: review surfacing failed for PR #%s", n, exc_info=True)
    if changed:
        _save_proposals(items)


def confirm_deployed():
    """For merged fixes whose post-deploy watch window has elapsed: if the incident did
    NOT recur, resolve the belief WITH evidence; if it did, the fix didn't hold -- refute
    and reopen the cluster. The incident ledger is the post-deploy verifier."""
    items = _load_proposals()
    changed = False
    now_iso = _now_iso()
    for p in items:
        if p.get("resolved") or not p.get("deploy_watch_until"):
            continue
        if now_iso < p["deploy_watch_until"]:
            continue  # still watching
        n = p["pr_number"]
        # p["held"] is the settled verdict downstream readers (argo_evolve's
        # outcome sync) consume directly, so it never has to be re-derived from
        # belief-id plumbing that may not line up.
        if not p.get("incident_key"):
            # Not failure-driven (e.g. an evolution upgrade): there is no incident
            # to check for recurrence, so don't claim "it held" -- just record the
            # quiet first day and leave the benefit claim to its dated prediction.
            p["held"] = True
            argo_self.resolve_self_belief(
                p["belief_id"],
                f"PR #{n} merged, CI green, quiet first day after deploy "
                f"(no incident check applies)")
            _send(f"the upgrade (PR #{n}) merged and nothing broke in the first "
                  f"day. my dated prediction will grade the real benefit.")
        elif argo_incidents.recurred_since(p["incident_key"], p.get("merged_at") or ""):
            p["held"] = False
            argo_self.add_evidence(
                p["belief_id"], f"incident recurred after PR #{n} merged", supports=False)
            argo_incidents.mark(p["incident_key"], status="open")
            _send(f"that fix (PR #{n}) didn't hold, the problem came back. reopening it "
                  f"so i can rethink the fix.")
        else:
            p["held"] = True
            argo_self.resolve_self_belief(
                p["belief_id"],
                f"PR #{n} merged, CI green, no recurrence since {p.get('merged_at')}")
            argo_incidents.mark(p["incident_key"], status="resolved",
                                resolved_commit=p.get("head_sha"))
            _send(f"the fix (PR #{n}) held: no recurrence since it merged. marking it "
                  f"resolved.")
        p["resolved"] = True
        changed = True
    if changed:
        _save_proposals(items)


def run_cli():
    """Scheduler entrypoint ('diagnose' command): prune, close the loop on prior fixes,
    then run one diagnose funnel. Each stage is independently guarded."""
    argo_incidents.prune(max_age_days=PRUNE_DAYS)
    try:
        verify_open_proposals()
    except Exception:
        log.error("diagnose: verify_open_proposals failed", exc_info=True)
    try:
        confirm_deployed()
    except Exception:
        log.error("diagnose: confirm_deployed failed", exc_info=True)
    try:
        result = diagnose()
    except Exception:
        log.error("diagnose: funnel failed", exc_info=True)
        result = {"acted": False, "reason": "error"}
    print(f"Diagnose: {result}")
    return result


if __name__ == "__main__":
    run_cli()
