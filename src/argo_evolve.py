"""Argo's frontier-evolution loop: 'watch -> map -> EVOLVE -> PR -> score'.

The diagnose loop (argo_diagnose) reacts to FAILURES; this loop reacts to the
FRONTIER ITSELF. On a schedule it watches release feeds for Argo's own stack
(models, SDKs, MCP -- data/frontier_feeds.json), maps anything new against an
honest self-description (data/stack_manifest.json + the live capability inventory
+ the self/world belief stores), and, at most once a day, texts the owner ONE
upgrade lever: "Anthropic shipped X, i could adopt it in Y -- reply EVOLVE or
SKIP." EVOLVE rehearses big levers with Argo's own adversaries (a KILL is final),
then drafts the change as a real PR through the existing propose machinery; a
dated prediction is recorded at accept, ARMED when the PR merges, and scored by
argo_predictions when due -- so an adopted upgrade has to prove itself against
reality, not vibes.

Everything dangerous is reused, not re-rolled: the PR path is argo_mcp_server's
_run_propose_fix (repro-test gate, protected-path denylist, PR-only token, human
merge -- Argo still can't merge anything); CI-polling and the post-deploy quiet
window are argo_diagnose's verify/confirm, which handle these PRs unchanged.

A second, inward funnel (scan_gaps / the 'gaps' command) reuses this exact spine
but reacts to Argo's OWN gaps instead of the external frontier: the proactive
capability-gap proposer. Its signal is the honest "not used" list in the stack
manifest plus Argo's unresolved self-beliefs. Same lever ledger, same EVOLVE/SKIP
gate, same rehearse/propose/predict path and shared one-nudge-a-day budget; levers
it mints are tagged source="gap".

Placement: these commands need the webhook's filesystem (volume ledgers + the
staging file the EVOLVE gate reads), so they run in the webhook's in-process
scheduler (argo_scheduled.local_loop). On GitHub Actions they are structurally
inert (the guard in run_cli/run_gaps_cli), exactly like diagnose is there.

Run:  python3 src/argo_evolve.py            (one full pass: sync, score, scan)
      python3 src/argo_evolve.py --gaps     (one inward capability-gap scan)
      python3 src/argo_evolve.py --no-send  (dry run: fetch + map + print only,
                                             no sends, no writes)
"""

import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone

import argo_paths
import argo_predictions
import argo_self
import argo_store
import argo_watch  # _item_id (pure) -- the seen-store identity this loop mirrors
import fetch_signals
import world_model
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported so tests patch the module globals (mock.patch.object); every helper
# reads the bare name at call time so the override bites.
FRONTIER_SEEN_PATH = argo_paths.FRONTIER_SEEN_PATH
EVOLUTION_PATH = argo_paths.EVOLUTION_PATH
PENDING_EVOLVE_PATH = argo_paths.PENDING_EVOLVE_PATH
MANIFEST_PATH = argo_paths.DATA / "stack_manifest.json"
ROOT = argo_paths.ROOT

MAX_NUDGES_PER_DAY = 1     # hard ceiling on proactive evolution nudges (spam guard)
MAX_ATTEMPTS = 3           # re-map an unused item this many times before retiring
MAP_ITEM_CAP = 20          # cap the mapper prompt size
MAX_AFFECTED_FILES = 3     # an evolution PR stays small and reviewable
MUTE_DAYS_SKIP = 30        # user said SKIP: rest a month
MUTE_DAYS_KILL = 60        # the rehearsal judge said KILL: rest two months
MUTE_DAYS_FAILED = 7       # authoring/CI failed: rest a week, then eligible again
STALE_CLAIM_HOURS = 2      # a claimed lever with no PR after this is a crashed accept
SEEN_CAP = 1000            # keep the seen-store bounded
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Levers in one of these states block re-proposing their feature slug; a terminal
# lever blocks only while muted (confirmed blocks forever -- it's adopted).
_IN_FLIGHT = ("nudge-ready", "nudged", "evolving", "accepted", "pr_open",
              "merged_watch")
_TERMINAL = ("rejected", "killed", "failed")

# Webhook updates run in parallel threads (one per Telegram message), so the
# EVOLVE/SKIP gate serializes its peek-clear-claim section: without it, two
# overlapping replies could both claim the same staged lever.
_GATE_LOCK = threading.Lock()

# Lever ids a live accept thread in THIS process is working on. The stale-claim
# sweep exists to recover claims orphaned by a process death -- after a restart
# this set is empty, so membership shields live work from the sweep no matter how
# slow rehearse/propose run, while true orphans still get re-armed.
_ACTIVE_CLAIMS = set()


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().strftime(_TS_FMT)


def _mute_until(days):
    return (_now() + timedelta(days=days)).strftime(_TS_FMT)


# --- small seams (patched in tests) ------------------------------------------

def _send(text):
    """Best-effort Telegram delivery (mirrors argo_diagnose._send)."""
    try:
        import send_telegram
        return send_telegram.try_send_message(text)
    except Exception:
        log.error("evolve: send failed", exc_info=True)
        return False


def _propose(payload):
    """Open the upgrade PR through the existing self-fix path: author files with the
    premium model, run the repro-test + wiring gates, open the PR, record it in the
    proposals ledger. Returns (text, info_or_None); info carries the PR number when
    one actually opened. Lazy import keeps the no-EVOLVE path light."""
    import argo_mcp_server
    return argo_mcp_server._run_propose_fix(payload, return_info=True)


# --- seen-store (own namespace; argo_watch's shape and identity) --------------

def load_seen():
    data = argo_store.load_json(FRONTIER_SEEN_PATH, {})
    if isinstance(data, list):  # tolerate the legacy list shape, like argo_watch
        return {i: MAX_ATTEMPTS for i in data}
    return data if isinstance(data, dict) else {}


def save_seen(seen):
    bounded = dict(list(seen.items())[-SEEN_CAP:])
    FRONTIER_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(FRONTIER_SEEN_PATH, bounded)


def _collect_new(seen):
    """Fetch the frontier feeds and return items still eligible for mapping (never
    seen, or seen but not yet settled)."""
    new = []
    for label, url in fetch_signals.load_frontier_feeds():
        for item in fetch_signals.fetch_feed(label, url):
            iid = argo_watch._item_id(item)
            if iid and seen.get(iid, 0) < MAX_ATTEMPTS:
                item["_iid"] = iid
                new.append(item)
    return new[:MAP_ITEM_CAP]


# --- the lever ledger ---------------------------------------------------------

def _load_ledger():
    data = argo_store.load_json(EVOLUTION_PATH, {})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("_meta"), dict):
        data["_meta"] = {}
    if not isinstance(data.get("levers"), list):
        data["levers"] = []
    return data


def _save_ledger(data):
    EVOLUTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(EVOLUTION_PATH, data)


def _next_id(levers):
    nums = []
    for l in levers:
        lid = str(l.get("id", ""))
        if lid.startswith("EV-") and lid.split("-", 1)[1].isdigit():
            nums.append(int(lid.split("-", 1)[1]))
    return f"EV-{max(nums, default=0) + 1:03d}"


def get_lever(lever_id):
    return next((l for l in _load_ledger()["levers"] if l.get("id") == lever_id), None)


def _update_lever(lever_id, **fields):
    data = _load_ledger()
    lever = next((l for l in data["levers"] if l.get("id") == lever_id), None)
    if lever is None:
        return None
    lever.update(fields)
    _save_ledger(data)
    return lever


def _active_features():
    """Feature slugs the mapper must not re-propose: anything in flight, anything
    adopted (confirmed), and any terminal lever still inside its mute window."""
    now = _now_iso()
    out = set()
    for l in _load_ledger()["levers"]:
        feature = l.get("feature")
        if not feature:
            continue
        status = l.get("status")
        if status in _TERMINAL:
            mu = l.get("muted_until")
            if mu and mu > now:
                out.add(feature)
        else:  # in flight or confirmed
            out.add(feature)
    return out


def _nudge_budget_left():
    meta = _load_ledger()["_meta"]
    today = _now().strftime("%Y-%m-%d")
    if meta.get("last_nudge_date") != today:
        return MAX_NUDGES_PER_DAY
    return max(0, MAX_NUDGES_PER_DAY - int(meta.get("nudges_today", 0)))


def _record_nudge():
    data = _load_ledger()
    meta = data["_meta"]
    today = _now().strftime("%Y-%m-%d")
    if meta.get("last_nudge_date") != today:
        meta["last_nudge_date"] = today
        meta["nudges_today"] = 0
    meta["nudges_today"] = int(meta.get("nudges_today", 0)) + 1
    _save_ledger(data)


# --- EVOLVE/SKIP staging (single slot, separate from the heal slot) -----------

def has_pending():
    return PENDING_EVOLVE_PATH.exists()


def _stage(lever_id):
    PENDING_EVOLVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(PENDING_EVOLVE_PATH,
                         {"lever_id": lever_id, "staged_at": _now_iso()})


def _peek_pending():
    data = argo_store.load_json(PENDING_EVOLVE_PATH, None)
    return data.get("lever_id") if isinstance(data, dict) else None


def _clear_pending():
    PENDING_EVOLVE_PATH.unlink(missing_ok=True)


# --- the mapper: one guarded model call ----------------------------------------

_MAP_SYSTEM = ("You are Argo, scanning frontier release notes for ONE concrete "
               "upgrade to your own stack. Be skeptical: most items are not relevant "
               "to you. Never invent a capability, and only name files from the "
               "provided list.")

_MAP_PROMPT = (
    "NEW frontier items (releases, changelogs, announcements):\n{items}\n\n"
    "YOUR CURRENT STACK (stack_manifest.json):\n{manifest}\n\n"
    "WHAT YOU BELIEVE ABOUT YOURSELF:\n{self_beliefs}\n\n"
    "WHAT YOU BELIEVE ABOUT THE FRONTIER:\n{world}\n\n"
    "Features already proposed or adopted (do NOT propose these again):\n{taken}\n\n"
    "Your source files (affected_files may only use paths from this list):\n{files}\n\n"
    "If exactly one of the NEW items unlocks a concrete, small upgrade to your own "
    "stack, reply with ONLY a JSON object, no prose, no markdown, with these keys:\n"
    '  "relevant": true\n'
    '  "feature": short snake_case slug for the capability (e.g. "structured_outputs")\n'
    '  "lever": one plain sentence: the concrete change to make\n'
    '  "affected_files": a list of 1-3 paths from the list above\n'
    '  "expected_benefit": one plain sentence\n'
    '  "risk": one plain sentence\n'
    '  "magnitude": "minor" for a contained change, "major" for a new call path or '
    "new dependency surface\n"
    '  "source_title": the title of the item that triggered this\n'
    "If nothing is genuinely relevant to YOUR stack (most runs), reply with exactly: "
    "NONE\nNo em dashes."
)


def _repo_files():
    try:
        return sorted("src/" + p.name for p in (ROOT / "src").glob("*.py"))
    except OSError:
        return []


def _resolve_model():
    import argo_observe as observe
    candidates = ([os.environ.get("ARGO_CHAT_MODEL") or "claude-sonnet-4-6"]
                  + observe.resolve_models())
    for m in candidates:
        p = observe.provider_for(m)
        if p and os.environ.get(p["key_env"]):
            return m
    return None


def _parse_json(raw):
    """Pull the first JSON object out of the model reply, tolerantly. None on failure.
    (Upgrading this very parser to structured outputs is seed lever EV: see
    ensure_seeds -- the loop's first proposal is to improve its own parsing.)"""
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


def _map_levers(items):
    """One guarded model call: new frontier items -> ONE lever dict, {} for an
    explicit NONE, or None on infrastructure failure (so the caller does not
    penalize the items' attempt counts for an outage). Routed through argo_observe
    so the DailyBudget + circuit breaker apply."""
    import argo_observe as observe
    model = _resolve_model()
    if model is None:
        log.warning("evolve: no model available for the mapper")
        return None
    listing = "\n".join(
        f"- [{it.get('source', '')}] {it.get('title', '')}: "
        f"{(it.get('summary') or '')[:200]}"
        for it in items)
    manifest = json.dumps(argo_store.load_json(MANIFEST_PATH, {}), indent=2)[:4000]
    prompt = _MAP_PROMPT.format(
        items=listing or "(none)",
        manifest=manifest,
        self_beliefs=argo_self.format_self_for_prompt() or "(none yet)",
        world=world_model.format_beliefs_for_prompt() or "(none yet)",
        taken=", ".join(sorted(_active_features())) or "(none)",
        files="\n".join(_repo_files()) or "(unavailable)")
    try:
        if observe.provider_for(model)["name"] == "anthropic":
            raw = observe.chat_with_mcp(
                _MAP_SYSTEM, [{"role": "user", "content": prompt}], model,
                temperature=0)
        else:
            raw = observe.generate_observations(prompt, model, temperature=0)
    except Exception:
        log.error("evolve: mapper call failed", exc_info=True)
        return None
    if (raw or "").strip().upper() == "NONE":
        return {}
    # A reply that is neither NONE nor parseable JSON is a model failure, not a
    # verdict: return None so the caller doesn't burn the items' attempt counts.
    return _parse_json(raw)


def _slug(s):
    return re.sub(r"[^a-z0-9_]+", "_", (s or "").strip().lower()).strip("_")[:40]


# --- the nudge -----------------------------------------------------------------

def _nudge_text(lever):
    src = ((lever.get("source_item") or {}).get("title") or "").strip()
    if lever.get("source") == "gap":
        # The inward twin: this lever came from my own gap list, not a release.
        head = (f"i spotted a gap in myself: {src}. " if src
                else "i spotted a gap in myself. ")
    else:
        head = (f"frontier update: {src}. " if src else "frontier idea: ")
    return (head
            + f"i could adopt {lever.get('feature')}: {lever.get('lever')} "
            + f"expected benefit: {lever.get('expected_benefit')} "
            + f"risk: {lever.get('risk')} "
            + "reply EVOLVE to draft the PR (i'll stress-test it with my own "
              "adversaries first if it's a big change), or SKIP to drop it. "
              "i can't merge anything myself.")


def _offer(lever_id):
    """Stage one lever behind the EVOLVE/SKIP gate and send the nudge. Seeds the
    self-belief at nudge time (low confidence; it must earn its way up)."""
    lever = get_lever(lever_id)
    if lever is None:
        return {"acted": False, "reason": "lever missing"}
    bid = lever.get("self_belief_id") or argo_self.add_self_belief(
        f"Adopting {lever.get('feature')} would improve me: "
        f"{lever.get('expected_benefit', '')}",
        kind="capability", source="evolution")
    # Persist the belief id before the send so a delivery-failure retry reuses it
    # instead of minting a duplicate belief.
    _update_lever(lever_id, self_belief_id=bid)
    sent = _send(_nudge_text(lever))
    if not sent:
        log.warning("evolve: nudge delivery failed for %s; will retry", lever_id)
        return {"acted": False, "reason": "nudge delivery failed", "lever": lever_id}
    _stage(lever_id)
    _record_nudge()
    # nudged_at lets the sweep spot a nudged lever whose staging slot was lost
    # (crashed gate turn) and re-arm it instead of wedging the feature forever.
    _update_lever(lever_id, status="nudged", nudged_at=_now_iso())
    log.info("evolve: staged + nudged %s (%s)", lever_id, lever.get("feature"))
    return {"acted": True, "lever": lever_id, "feature": lever.get("feature")}


# --- the scan funnel (free gates before the paid one) ---------------------------

def _sweep_stale_claims():
    """Re-arm levers wedged by a crash so the funnel re-offers them:
    - 'evolving'/'accepted' with no PR past the lease (death mid-rehearse/propose);
    - 'nudged' with no staging slot pointing at it (a gate turn cleared the slot,
      then died before writing the next status). A properly staged lever waits for
      the user indefinitely -- only the orphaned ones are revived.
    Levers in _ACTIVE_CLAIMS are a live thread's work in this process and are never
    swept (the time lease alone only backstops a scan running in a different
    process than the webhook gate). Runs under the gate lock so it can't fire
    inside another thread's clear-then-claim window."""
    cutoff = (_now() - timedelta(hours=STALE_CLAIM_HOURS)).strftime(_TS_FMT)
    with _GATE_LOCK:
        staged = _peek_pending()
        data = _load_ledger()
        changed = False
        for l in data["levers"]:
            lid = l.get("id")
            if lid in _ACTIVE_CLAIMS or lid == staged:
                continue
            status = l.get("status")
            stuck_claim = (status in ("evolving", "accepted")
                           and not l.get("pr_number")
                           and (l.get("claimed_at") or "") < cutoff)
            orphaned_nudge = (status == "nudged"
                              and (l.get("nudged_at") or "") < cutoff)
            if stuck_claim or orphaned_nudge:
                log.warning("evolve: re-arming wedged %s lever %s (%s)",
                            status, lid, l.get("feature"))
                l["status"] = "nudge-ready"
                changed = True
        if changed:
            _save_ledger(data)


def _match_item(items, source_title):
    """Link the mapper's source_title back to ONE feed item: exact normalized
    match first, containment only when it's unambiguous. A short partial title
    must not attach the lever (or settle the seen-store) for the wrong release."""
    t = (source_title or "").strip().lower()
    if not t:
        return None
    exact = [it for it in items if (it.get("title") or "").strip().lower() == t]
    if exact:
        return exact[0]
    partial = [it for it in items if t in (it.get("title") or "").lower()]
    return partial[0] if len(partial) == 1 else None


def scan():
    """Run the funnel once. Returns a summary dict (never raises out to the
    scheduler). Gate order keeps every free check ahead of the one paid call."""
    # Recover claims orphaned by a crash before the gates run (see the sweep).
    _sweep_stale_claims()
    # GATE 1: one staged lever at a time -- the webhook gate must resolve it first.
    if has_pending():
        return {"acted": False, "reason": "pending lever awaiting EVOLVE/SKIP"}
    # GATE 2: the spam ceiling.
    if _nudge_budget_left() <= 0:
        return {"acted": False, "reason": "daily nudge budget spent"}
    # GATE 3 (free): a seeded lever ready to offer skips fetch + mapper entirely.
    seed = next((l for l in _load_ledger()["levers"]
                 if l.get("status") == "nudge-ready"), None)
    if seed:
        return _offer(seed["id"])
    # Fetch + dedup (network, but no model cost).
    seen = load_seen()
    items = _collect_new(seen)
    if not items:
        return {"acted": False, "reason": "no new frontier items"}
    result = _map_levers(items)
    if result is None:
        # Infrastructure failure: do NOT update the seen-store, so the items are
        # not attempt-penalized for an outage (mirrors argo_watch's abort).
        return {"acted": False, "reason": "mapper unavailable"}
    # The mapper ran: bump every item it saw. The chosen item is settled at
    # MAX_ATTEMPTS only after a lever actually lands, so a mapper hit that the
    # validation gates below reject is retried (up to the cap), not retired.
    for it in items:
        seen[it["_iid"]] = seen.get(it["_iid"], 0) + 1
    save_seen(seen)
    if not result.get("relevant"):
        return {"acted": False, "reason": "nothing relevant"}
    feature = _slug(result.get("feature"))
    if not feature:
        return {"acted": False, "reason": "mapper returned no feature slug"}
    if feature in _active_features():
        return {"acted": False, "reason": f"feature already tracked: {feature}"}
    files = [f for f in (result.get("affected_files") or [])
             if isinstance(f, str) and (ROOT / f).exists()]
    if not files or len(files) > MAX_AFFECTED_FILES:
        return {"acted": False, "reason": "affected files missing or too many"}
    magnitude = result.get("magnitude") if result.get("magnitude") in ("minor", "major") else "major"
    src_item = _match_item(items, result.get("source_title"))
    data = _load_ledger()
    lever = {
        "id": _next_id(data["levers"]), "created_at": _now_iso(),
        "source": "frontier",
        "source_item": ({"title": src_item.get("title"), "link": src_item.get("link"),
                         "feed": src_item.get("source")} if src_item else
                        {"title": result.get("source_title")}),
        "feature": feature, "lever": (result.get("lever") or "").strip(),
        "affected_files": files,
        "expected_benefit": (result.get("expected_benefit") or "").strip(),
        "risk": (result.get("risk") or "").strip(),
        # nudge-ready (not "new") so a failed nudge send is retried by GATE 3
        # next scan instead of stranding the lever and blocking its feature.
        "magnitude": magnitude, "status": "nudge-ready", "muted_until": None,
        "self_belief_id": None, "world_belief_id": None,
        "prediction_id": None, "prediction_spec": None,
        "pr_number": None, "rehearse": None,
    }
    data["levers"].append(lever)
    _save_ledger(data)
    if src_item:
        seen[src_item["_iid"]] = MAX_ATTEMPTS
        save_seen(seen)
    return _offer(lever["id"])


# --- the proactive capability-gap proposer (the inward twin of scan) -------------
#
# scan() reacts to the EXTERNAL frontier (new releases). This reacts to the gap
# between what Argo IS and what it could be: the honest "not used" list in the
# stack manifest plus Argo's own unresolved self-beliefs. It feeds the SAME lever
# ledger and the SAME EVOLVE/SKIP gate, and draws from the same one-pending /
# one-nudge-a-day guards via the shared _meta -- only the signal source differs, so
# an internal-gap lever rehearses, proposes, and scores exactly like a frontier
# one. Levers it mints carry source="gap".

_GAP_SYSTEM = ("You are Argo, looking inward for ONE concrete upgrade that closes a "
               "real gap in your own capabilities. Be skeptical and honest: propose "
               "nothing speculative, never invent a capability, and only name files "
               "from the provided list. Most runs, nothing is worth a change.")

_GAP_PROMPT = (
    "KNOWN GAPS IN YOURSELF (your stack manifest's 'not used' list and your own "
    "unresolved self-beliefs):\n{gaps}\n\n"
    "YOUR CURRENT STACK (stack_manifest.json):\n{manifest}\n\n"
    "WHAT YOU BELIEVE ABOUT YOURSELF:\n{self_beliefs}\n\n"
    "Features already proposed or adopted (do NOT propose these again):\n{taken}\n\n"
    "Your source files (affected_files may only use paths from this list):\n{files}\n\n"
    "If exactly one of these gaps is worth closing now with a concrete, small, "
    "self-contained change, reply with ONLY a JSON object, no prose, no markdown, "
    "with these keys:\n"
    '  "relevant": true\n'
    '  "gap": the gap you are closing, in a few words\n'
    '  "feature": short snake_case slug for the capability (e.g. '
    '"usage_cost_telemetry")\n'
    '  "lever": one plain sentence: the concrete change to make\n'
    '  "affected_files": a list of 1-3 paths from the list above\n'
    '  "expected_benefit": one plain sentence\n'
    '  "risk": one plain sentence\n'
    '  "magnitude": "minor" for a contained change, "major" for a new call path or '
    "new dependency surface\n"
    "If no gap is worth a change right now (most runs), reply with exactly: NONE\n"
    "No em dashes."
)


def _collect_gaps():
    """The inward signal source (pure read, no model call): the honest 'not used'
    list from the stack manifest plus Argo's own unresolved self-beliefs of kind
    'issue'. Returns plain-string descriptions for the mapper to choose ONE from."""
    gaps = []
    manifest = argo_store.load_json(MANIFEST_PATH, {})
    for g in manifest.get("api_features_not_used") or []:
        if isinstance(g, str) and g.strip():
            gaps.append(f"stack gap: {g.strip()}")
    try:
        for b in argo_self.get_self_beliefs(kind="issue"):
            if b.get("status") == "resolved":
                continue
            gaps.append(f"self issue (confidence {b.get('confidence')}): "
                        f"{b.get('claim')}")
    except Exception:
        log.error("evolve: reading self-beliefs for gaps failed", exc_info=True)
    return gaps


def _map_gap(gaps):
    """One guarded model call (mirrors _map_levers): known gaps -> ONE lever dict,
    {} for an explicit NONE, or None on infrastructure failure (so a NONE and an
    outage stay distinguishable). Routed through argo_observe so the DailyBudget +
    circuit breaker apply."""
    import argo_observe as observe
    model = _resolve_model()
    if model is None:
        log.warning("evolve: no model available for the gap mapper")
        return None
    prompt = _GAP_PROMPT.format(
        gaps="\n".join(f"- {g}" for g in gaps) or "(none)",
        manifest=json.dumps(argo_store.load_json(MANIFEST_PATH, {}), indent=2)[:4000],
        self_beliefs=argo_self.format_self_for_prompt() or "(none yet)",
        taken=", ".join(sorted(_active_features())) or "(none)",
        files="\n".join(_repo_files()) or "(unavailable)")
    try:
        if observe.provider_for(model)["name"] == "anthropic":
            raw = observe.chat_with_mcp(
                _GAP_SYSTEM, [{"role": "user", "content": prompt}], model,
                temperature=0)
        else:
            raw = observe.generate_observations(prompt, model, temperature=0)
    except Exception:
        log.error("evolve: gap mapper call failed", exc_info=True)
        return None
    if (raw or "").strip().upper() == "NONE":
        return {}
    # Neither NONE nor parseable JSON is a model failure, not a verdict: None.
    return _parse_json(raw)


def scan_gaps():
    """Run the inward funnel once (the proactive capability-gap proposer). Same
    free-gates-before-the-paid-call order as scan(); returns a summary dict, never
    raises out to the scheduler."""
    # Recover claims orphaned by a crash before the gates run (shared with scan()).
    _sweep_stale_claims()
    # GATE 1: one staged lever at a time -- shared with the frontier funnel, so a
    # frontier lever awaiting EVOLVE/SKIP also blocks the gap funnel (and vice versa).
    if has_pending():
        return {"acted": False, "reason": "pending lever awaiting EVOLVE/SKIP"}
    # GATE 2: the shared daily nudge ceiling (frontier and gaps draw one budget, so
    # the two funnels together still nudge at most once a day).
    if _nudge_budget_left() <= 0:
        return {"acted": False, "reason": "daily nudge budget spent"}
    # GATE 3 (free): a gap lever already minted but undelivered (a prior send failed)
    # is re-offered without burning a fresh model call.
    ready = next((l for l in _load_ledger()["levers"]
                  if l.get("status") == "nudge-ready" and l.get("source") == "gap"),
                 None)
    if ready:
        return _offer(ready["id"])
    gaps = _collect_gaps()
    if not gaps:
        return {"acted": False, "reason": "no open capability gaps"}
    result = _map_gap(gaps)
    if result is None:
        return {"acted": False, "reason": "gap mapper unavailable"}
    if not result.get("relevant"):
        return {"acted": False, "reason": "no gap worth closing this run"}
    feature = _slug(result.get("feature"))
    if not feature:
        return {"acted": False, "reason": "gap mapper returned no feature slug"}
    if feature in _active_features():
        return {"acted": False, "reason": f"feature already tracked: {feature}"}
    files = [f for f in (result.get("affected_files") or [])
             if isinstance(f, str) and (ROOT / f).exists()]
    if not files or len(files) > MAX_AFFECTED_FILES:
        return {"acted": False, "reason": "affected files missing or too many"}
    magnitude = result.get("magnitude") if result.get("magnitude") in ("minor", "major") else "major"
    data = _load_ledger()
    lever = {
        "id": _next_id(data["levers"]), "created_at": _now_iso(),
        "source": "gap",
        "source_item": {"title": (result.get("gap") or "").strip()
                        or "an internal capability gap"},
        "feature": feature, "lever": (result.get("lever") or "").strip(),
        "affected_files": files,
        "expected_benefit": (result.get("expected_benefit") or "").strip(),
        "risk": (result.get("risk") or "").strip(),
        # nudge-ready (not "new") so a failed nudge send is retried by GATE 3 next
        # scan, mirroring the frontier funnel.
        "magnitude": magnitude, "status": "nudge-ready", "muted_until": None,
        "self_belief_id": None, "world_belief_id": None,
        # No precomputable metric for a gap lever, so no dated prediction -- the
        # benefit lands as belief evidence, honestly unscored (like prompt_caching).
        "prediction_id": None, "prediction_spec": None,
        "pr_number": None, "rehearse": None,
    }
    data["levers"].append(lever)
    _save_ledger(data)
    return _offer(lever["id"])


# --- EVOLVE / SKIP (consumed by the webhook gate) --------------------------------

def decline_pending():
    """User replied SKIP: drop the staged lever and mute its feature for a month.
    Peek-clear-update runs under the gate lock so a SKIP can't race an EVOLVE
    thread that is mid-claim on the same lever."""
    with _GATE_LOCK:
        lid = _peek_pending()
        _clear_pending()
        if not lid:
            # An EVOLVE thread may have claimed the lever already (it clears the
            # staging at claim time); tell the user instead of a bare "nothing".
            busy = next((l for l in _load_ledger()["levers"]
                         if l.get("status") == "evolving"), None)
            if busy:
                return (f"I'm already mid-evolve on {busy.get('feature')} (you said "
                        "EVOLVE first). If the PR opens and you don't want it, just "
                        "close it unmerged.")
            return "Nothing staged to skip right now."
        lever = _update_lever(lid, status="rejected",
                              muted_until=_mute_until(MUTE_DAYS_SKIP))
    bid = (lever or {}).get("self_belief_id")
    if bid:
        argo_self.add_evidence(bid, "user skipped the proposal", supports=False)
    feature = (lever or {}).get("feature") or "that one"
    return f"Dropped it. I won't bring up {feature} again for a month."


def _judge_reason(judge_text):
    for line in (judge_text or "").splitlines():
        if line.strip().upper().startswith("VERDICT:"):
            return line.strip()
    return (judge_text or "").strip()[:200]


def _rehearse_lever(lever):
    """Run the upgrade bet through the existing adversaries + judge (text-based:
    zero changes to argo_rehearse). Returns (verdict, notes) or (None, reason) on
    infrastructure failure."""
    import argo_rehearse
    bet = ("UPGRADE BET (a change to Argo's own stack, not a user project):\n"
           f"Adopt {lever.get('feature')}: {lever.get('lever')}\n"
           f"Expected benefit: {lever.get('expected_benefit')}\n"
           f"Risk: {lever.get('risk')}\n"
           f"Files to change: {', '.join(lever.get('affected_files') or [])}")
    run_id = f"{lever.get('id', 'EV-???')}-{_now():%Y%m%dT%H%M%S}"
    try:
        critiques = argo_rehearse.run_adversaries(bet, run_id, lever.get("id", ""))
        if critiques is None:
            return None, "no model available"
        verdict, judge_text = argo_rehearse.run_judge(bet, critiques, run_id,
                                                      lever.get("id", ""))
        if verdict is None:
            return None, (judge_text or "judge failed")[:200]
        return verdict, _judge_reason(judge_text)
    except Exception as exc:
        log.error("evolve: rehearsal failed", exc_info=True)
        return None, type(exc).__name__


def accept_pending():
    """User replied EVOLVE: rehearse (major levers), record the world-model belief
    + dated prediction, then draft the PR through the existing propose path.
    Returns the honest text for the webhook to send. The peek-clear-claim section
    runs under the gate lock and flips the lever to 'evolving', so an overlapping
    EVOLVE or SKIP can't double-process the slow rehearse/propose work below."""
    with _GATE_LOCK:
        lid = _peek_pending()
        _clear_pending()
        if not lid:
            return "Nothing staged to evolve. I'll flag the next upgrade I spot."
        lever = get_lever(lid)
        if lever is None:
            return ("I lost track of that lever (the staging outlived the ledger). "
                    "I'll re-flag it if it still matters.")
        if lever.get("status") != "nudged":
            return (f"That lever is already {lever.get('status')}; nothing left "
                    "for me to do here.")
        # claimed_at is the lease: if this process dies mid-rehearse/propose,
        # _sweep_stale_claims re-arms the lever instead of leaving it stuck.
        _update_lever(lid, status="evolving", claimed_at=_now_iso())
        _ACTIVE_CLAIMS.add(lid)
    try:
        return _run_accept(lid, lever)
    finally:
        _ACTIVE_CLAIMS.discard(lid)


def _run_accept(lid, lever):
    """The slow half of EVOLVE (rehearse, belief + prediction, propose). Runs
    outside the gate lock; _ACTIVE_CLAIMS shields the claim from the stale sweep
    for however long this takes."""
    # Major levers must survive the debate first -- the same gate user projects get.
    if lever.get("magnitude") == "major":
        verdict, notes = _rehearse_lever(lever)
        if verdict is None:
            # Infrastructure failure: put it back exactly as staged so EVOLVE retries.
            _update_lever(lid, status="nudged")
            _stage(lid)
            return (f"I couldn't run the rehearsal ({notes}). The lever is still "
                    "staged; reply EVOLVE to retry.")
        _update_lever(lid, rehearse={"verdict": verdict, "notes": notes[:500]})
        if verdict == "KILL":
            _update_lever(lid, status="killed",
                          muted_until=_mute_until(MUTE_DAYS_KILL))
            bid = lever.get("self_belief_id")
            if bid:
                argo_self.add_evidence(bid, f"rehearsal killed it: {notes[:160]}",
                                       supports=False)
            return ("I argued with myself about it first and the judge said no. "
                    f"{notes[:300]} Dropping it for a couple of months.")
    # Adopted: earn a world-model belief, and a dated prediction when scorable.
    wm_id = world_model.add_belief(
        f"Adopting {lever.get('feature')} improves Argo: "
        f"{lever.get('expected_benefit', '')}",
        source_finding=f"evolution:{lid}")
    pred_id = None
    spec = lever.get("prediction_spec")
    if isinstance(spec, dict) and isinstance(spec.get("metric"), dict):
        pred_id = argo_predictions.record(
            wm_id, spec.get("text", ""), spec["metric"],
            int(spec.get("days", 14)), source=f"evolution:{lid}")
    _update_lever(lid, status="accepted", world_belief_id=wm_id,
                  prediction_id=pred_id)
    payload = {
        "title": f"Argo evolution: adopt {lever.get('feature')}",
        "description": (f"Frontier upgrade: {lever.get('lever', '')}\n\n"
                        f"Expected benefit: {lever.get('expected_benefit', '')}\n"
                        f"Risk: {lever.get('risk', '')}\n"
                        f"Evolution lever: {lid}."),
        "suspected_files": lever.get("affected_files") or [],
        "suggestion": lever.get("lever", ""),
        "belief_id": lever.get("self_belief_id"),
        "incident_key": None,  # not failure-driven; safe for verify/confirm as-is
        "kind": "evolution",
    }
    try:
        text, info = _propose(payload)
    except Exception:
        log.error("evolve: propose failed for %s", lid, exc_info=True)
        text, info = None, None
    if not info:
        _update_lever(lid, status="failed", muted_until=_mute_until(MUTE_DAYS_FAILED))
        return text or ("I tried to draft the upgrade PR but hit an error before "
                        "it opened. I'll let this one rest a week.")
    _ensure_proposal_row(info, lever.get("self_belief_id"))
    _update_lever(lid, status="pr_open", pr_number=info["pr_number"])
    return text


def _ensure_proposal_row(info, belief_id):
    """The propose path records the PR in the proposals ledger itself, but that
    write is best-effort; if it failed, re-record here so sync_proposal_outcomes
    can follow the PR instead of stranding the lever in pr_open forever."""
    import argo_diagnose
    n = info.get("pr_number")
    try:
        if any(p.get("pr_number") == n for p in argo_diagnose._load_proposals()):
            return
        argo_diagnose.append_proposal(n, info.get("url"), belief_id, None,
                                      head_sha=info.get("head_sha"))
        log.warning("evolve: proposals ledger was missing PR #%s; re-recorded", n)
    except Exception:
        log.error("evolve: could not ensure proposal row for PR #%s", n,
                  exc_info=True)


# --- closing the loop: follow the PR, then score the prediction ------------------

def sync_proposal_outcomes():
    """Read-only join against the proposals ledger that argo_diagnose's verify/
    confirm passes already maintain: move each evolution lever (and its world-model
    belief) to match its PR's fate, and ARM the prediction the moment the PR merges.
    Never raises."""
    try:
        import argo_diagnose
        proposals = {p.get("pr_number"): p for p in argo_diagnose._load_proposals()}
        data = _load_ledger()
        changed = False
        for lever in data["levers"]:
            n = lever.get("pr_number")
            if not n or lever.get("status") not in ("pr_open", "merged_watch"):
                continue
            p = proposals.get(n)
            if not p:
                continue
            wm_id = lever.get("world_belief_id")
            if p.get("ci_failed") and lever["status"] == "pr_open":
                lever["status"] = "failed"
                lever["muted_until"] = _mute_until(MUTE_DAYS_FAILED)
                if wm_id:
                    world_model.add_evidence(wm_id, f"PR #{n} failed CI",
                                             supports=False)
                changed = True
            elif p.get("resolved"):
                # diagnose's confirm pass finished its post-deploy quiet window and
                # settled the SELF-belief; mirror its verdict here. The dated
                # prediction (due later) stays the stronger, final grader.
                if lever.get("prediction_id") and p.get("merged_at"):
                    argo_predictions.arm(lever["prediction_id"], p.get("merged_at"))
                if "held" in p:
                    held = bool(p["held"])  # confirm_deployed's settled verdict
                else:
                    # Legacy proposal rows predate the held stamp: re-derive it
                    # from the self-belief that confirm_deployed resolved.
                    bid = lever.get("self_belief_id")
                    held = any(b.get("id") == bid and b.get("status") == "resolved"
                               for b in argo_self.get_self_beliefs())
                if held:
                    lever["status"] = "confirmed"
                    if wm_id:
                        world_model.add_evidence(
                            wm_id, f"PR #{n} merged; first day post-deploy "
                                   f"was quiet")
                else:
                    lever["status"] = "failed"
                    lever["muted_until"] = _mute_until(MUTE_DAYS_FAILED)
                    if wm_id:
                        world_model.add_evidence(wm_id, f"PR #{n} did not hold",
                                                 supports=False)
                changed = True
            elif p.get("merged") and lever["status"] == "pr_open":
                lever["status"] = "merged_watch"
                if lever.get("prediction_id"):
                    argo_predictions.arm(lever["prediction_id"],
                                         p.get("merged_at") or _now_iso())
                changed = True
        if changed:
            _save_ledger(data)
    except Exception:
        log.error("evolve: sync_proposal_outcomes failed", exc_info=True)


def _apply_prediction_verdicts():
    """The dated prediction is the loop's final grader: when score_due marks an
    evolution lever's prediction wrong, close the lever lifecycle too -- failed
    plus a rest week -- so a confirmed lever whose benefit never showed up stops
    blocking its feature slug forever. (world_model already took the scoring hit
    via apply_prediction_outcome; no extra evidence here.) Never raises."""
    try:
        data = _load_ledger()
        changed = False
        for lever in data["levers"]:
            pid = lever.get("prediction_id")
            if not pid or lever.get("status") not in ("confirmed", "merged_watch"):
                continue
            p = argo_predictions.get_prediction(pid)
            if not p or not p.get("scored_at") or p.get("correct") is not False:
                continue
            log.info("evolve: prediction %s scored wrong; failing lever %s",
                     pid, lever.get("id"))
            lever["status"] = "failed"
            lever["muted_until"] = _mute_until(MUTE_DAYS_FAILED)
            changed = True
        if changed:
            _save_ledger(data)
    except Exception:
        log.error("evolve: _apply_prediction_verdicts failed", exc_info=True)


# --- dogfood seeds ----------------------------------------------------------------

# Three pre-validated upgrades (researched 2026-06-10) so week one exercises the
# whole pipeline: nudge -> EVOLVE -> (rehearse) -> PR -> verify -> confirm -> score.
# Each is scoped to <=3 files; the structured-outputs seed ships its own measurement
# hook so its prediction has a precomputable cluster key.
_SEED_LEVERS = [
    {
        "feature": "structured_outputs",
        "lever": ("Adopt Anthropic structured outputs (output_config json_schema) "
                  "for my own JSON-returning calls: argo_observe grows an optional "
                  "output_schema param and argo_diagnose uses it so a malformed "
                  "reply can no longer silently drop a diagnosis. The change must "
                  "also call argo_incidents.record_incident('model_failure', "
                  "'diagnose json parse failed') whenever parsing still fails, so "
                  "the prediction below is measurable."),
        "affected_files": ["src/argo_observe.py", "src/argo_diagnose.py"],
        "expected_benefit": ("Schema-valid JSON from diagnosis calls; the silent "
                             "parse-failure class disappears."),
        "risk": ("The output_config parameter shape must match the current API or "
                 "the diagnose call 400s."),
        "magnitude": "minor",
        "prediction_spec": {
            "text": ("No 'diagnose json parse failed' incidents recur within 14 "
                     "days of the structured-outputs PR merging"),
            "metric": {"kind": "incident_absent",
                       "key": "model_failure|diagnose json parse failed"},
            "days": 14,
        },
    },
    {
        "feature": "prompt_caching",
        "lever": ("Add cache_control (prompt caching) to my Anthropic calls so the "
                  "big stable system prompt (capabilities + self beliefs + profile) "
                  "is cached between webhook turns instead of re-billed every "
                  "message; volatile context must stay after the cache breakpoint."),
        "affected_files": ["src/argo_observe.py", "src/argo_webhook.py"],
        "expected_benefit": ("Roughly 90 percent cheaper input tokens on repeated "
                             "chat turns, no behavior change."),
        "risk": ("A churning prompt prefix (timestamps, reordered sections) would "
                 "silently miss the cache; section order matters."),
        "magnitude": "minor",
        "prediction_spec": None,  # no usage telemetry yet -- benefit lands as
                                  # belief evidence, honestly unscored
    },
    {
        "feature": "batch_api",
        "lever": ("Move SEAS signal auto-scoring to the Anthropic Batch API (50 "
                  "percent cheaper, not latency sensitive), with a synchronous "
                  "fallback when a batch does not complete in time."),
        "affected_files": ["src/seas_finding.py", "src/argo_observe.py"],
        "expected_benefit": "Half-price scoring of the signal pool each run.",
        "risk": ("A new call path (submit, poll, collect) that can time out or "
                 "partially complete; the fallback must be wired."),
        "magnitude": "major",
        "prediction_spec": {
            "text": ("No scheduler_task_error incidents occur in the 14 days after "
                     "the batch-scoring PR merges"),
            "metric": {"kind": "incident_absent",
                       "incident_kind": "scheduler_task_error"},
            "days": 14,
        },
    },
]


def ensure_seeds():
    """Insert the dogfood seed levers once (idempotent by feature slug). The funnel
    offers one per day, so the seeds serialize naturally across week one."""
    try:
        data = _load_ledger()
        have = {l.get("feature") for l in data["levers"]}
        added = 0
        for seed in _SEED_LEVERS:
            if seed["feature"] in have:
                continue
            entry = {
                "id": _next_id(data["levers"]), "created_at": _now_iso(),
                "source": "seed", "source_item": None,
                "status": "nudge-ready", "muted_until": None,
                "self_belief_id": None, "world_belief_id": None,
                "prediction_id": None, "pr_number": None, "rehearse": None,
            }
            entry.update(seed)
            data["levers"].append(entry)
            added += 1
        if added:
            _save_ledger(data)
            log.info("evolve: seeded %d dogfood lever(s)", added)
        return added
    except Exception:
        log.error("evolve: ensure_seeds failed", exc_info=True)
        return 0


# --- entrypoints --------------------------------------------------------------

def run_cli():
    """Scheduler entrypoint (the 'frontier' command): close the loop on prior
    evolution PRs, score due predictions, then run one scan funnel. Each stage is
    independently guarded."""
    # Placement guard (load-bearing): on GitHub Actions there is no persistent
    # ledger and the webhook can never read the staging file -- structurally inert
    # there, the same way diagnose is. The webhook's local scheduler is the real
    # production home (argo_scheduled.local_loop).
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("ARGO_EVOLUTION_PATH"):
        log.info("frontier: skipping on Actions (no shared filesystem with the webhook)")
        print("Frontier: skipped on Actions (no shared filesystem with the webhook).")
        return {"acted": False, "reason": "actions-no-volume"}
    ensure_seeds()
    try:
        sync_proposal_outcomes()
    except Exception:
        log.error("frontier: sync failed", exc_info=True)
    try:
        argo_predictions.score_due(notify=_send)
    except Exception:
        log.error("frontier: prediction scoring failed", exc_info=True)
    _apply_prediction_verdicts()
    try:
        result = scan()
    except Exception:
        log.error("frontier: scan failed", exc_info=True)
        result = {"acted": False, "reason": "error"}
    print(f"Frontier: {result}")
    return result


def run_gaps_cli():
    """Scheduler entrypoint (the 'gaps' command): the proactive capability-gap
    proposer. Shares run_cli's placement guard -- it needs the webhook's volume
    ledger + the staging file the EVOLVE gate reads, so on Actions it is
    structurally inert. The closing-the-loop work (sync_proposal_outcomes,
    score_due) stays with the daily 'frontier' run so it is not doubled here; this
    command only adds the inward gap scan."""
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("ARGO_EVOLUTION_PATH"):
        log.info("gaps: skipping on Actions (no shared filesystem with the webhook)")
        print("Gaps: skipped on Actions (no shared filesystem with the webhook).")
        return {"acted": False, "reason": "actions-no-volume"}
    try:
        result = scan_gaps()
    except Exception:
        log.error("gaps: scan_gaps failed", exc_info=True)
        result = {"acted": False, "reason": "error"}
    print(f"Gaps: {result}")
    return result


def main():
    """CLI: full frontier pass by default; --gaps runs one inward capability-gap
    scan; --no-send is a pure read path (fetch + map + print the candidate; no
    sends, no staging, no seen-store or ledger writes)."""
    if "--gaps" in sys.argv:
        return run_gaps_cli()
    if "--no-send" not in sys.argv:
        return run_cli()
    seen = load_seen()
    items = _collect_new(seen)
    print(f"\n🧭 Argo Frontier (dry run) — {len(items)} new item(s)")
    if not items:
        print("No new frontier items.\n")
        return
    result = _map_levers(items)
    if result is None:
        print("Mapper unavailable (no model/key or call failed).\n")
        return
    if not result.get("relevant"):
        print("Mapper: nothing relevant to the stack this run.\n")
        return
    print("Candidate lever (nothing sent, staged, or recorded):")
    print(json.dumps(result, indent=2))
    print()


if __name__ == "__main__":
    main()
