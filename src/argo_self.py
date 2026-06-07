"""Argo's self-model: what Argo knows and believes about ITSELF.

Three layers, one cohesive module:

1. CAPABILITY SELF-KNOWLEDGE (live, self-updating). list_capabilities() reads the
   real MCP tool registry, so Argo's sense of "what I can do" can never drift from
   what it actually does -- a newly added @mcp.tool() shows up with no second edit.
   This is what fixes "Argo forgot it has Rehearse": rehearse_project is in the
   registry, so it is in the prompt.

2. SELF-BELIEF STORE (a self-model, mirroring world_model.py's shape). Beliefs ABOUT
   ARGO ITSELF -- known issues, lessons, confirmed capabilities -- that persist past
   the ~12-turn chat memory. The one inviolable rule, copied from the world model:
   **confidence moves ONLY via evidence, never by assertion.** There is no
   set_confidence(); add_evidence() is the only mutator that moves the number, so
   Argo can't launder "I think I'm fixed now" into confidence.

3. PERFORMANCE TRACKING + REFLECTION (the loop). reflect() reads the outcome data
   already on disk (project energy ratings, tripwire seen-store) and, only when
   there's something new, makes one cheap model call to distil at most two honest
   lessons into the self-belief store. Triggered by the scheduler ('reflect'
   command) and on demand (the run_reflection MCP tool).

Standard-library + the shared-utils layer (argo_store/argo_paths/argo_log). JSON
store at data/argo_self.json (gitignored; example committed as argo_self.example.json).
"""

import json
import os
import re
from datetime import datetime, timezone

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Re-exported so tests can patch the module global (mock.patch.object(argo_self,
# "SELF_PATH", tmp)); every helper reads the bare name at call time, so the
# override bites. Never read argo_paths.SELF_PATH directly inside a helper.
SELF_PATH = argo_paths.SELF_PATH
PROJECTS_LOG = argo_paths.PROJECTS_LOG
SEEN_PATH = argo_paths.SEEN_PATH

# Confidence is clamped open: a self-belief is never certain and never fully dead.
CONF_MIN, CONF_MAX = 0.05, 0.95
EVIDENCE_STEP = 0.05           # one piece of evidence nudges confidence this much
SEED_CONFIDENCE = 0.30         # a fresh belief enters here, unverified
SETTLED_ATTEMPTS = 3           # argo_watch.MAX_ATTEMPTS: a seen item at/over this is settled
RECENT_N = 5                   # how many recent projects count as "recent" energy
REFLECT_MIN_NEW = 2            # newly-rated projects needed before a reflection model call

_KINDS = ("issue", "lesson", "capability", "trait", "identity")
_META_ID = "SB-META"           # bookkeeping record (reflection marker), not a belief


# --- Layer 1: live capability self-knowledge --------------------------------

def _first_sentence(text, cap=120):
    """First sentence of a tool docstring, collapsed to one clean line (no markdown,
    no em dashes) and capped so the prompt block stays compact."""
    text = " ".join((text or "").split()).replace("—", "-").replace("–", "-")
    if not text:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    if len(s) > cap:
        s = s[:cap].rstrip() + "..."
    return s


def list_capabilities():
    """Argo's REAL tool inventory as [{"name", "summary"}], read live from the MCP
    registry so it can never drift from what Argo can actually do. Returns [] on any
    failure, so the prompt degrades gracefully rather than bricking the bot."""
    try:
        import argo_mcp_server
        tools = argo_mcp_server.mcp._tool_manager.list_tools()
    except Exception:
        log.warning("could not read the MCP tool registry for self-inventory",
                    exc_info=True)
        return []
    out = []
    for t in tools:
        name = getattr(t, "name", "") or ""
        if not name:
            continue
        out.append({"name": name,
                    "summary": _first_sentence(getattr(t, "description", ""))})
    return sorted(out, key=lambda c: c["name"])


def format_capabilities_for_prompt():
    """A compact, plain-text capability block for the system prompt, or "" if the
    registry can't be read. The closing instruction is what makes Argo recite its
    REAL tools instead of half-remembering them."""
    caps = list_capabilities()
    if not caps:
        return ""
    lines = ["YOUR CURRENT TOOLS (generated live from your own registry -- this is "
             "your COMPLETE tool list; when asked what you can do, recite from THIS, "
             "and if a tool is not listed here you do not have it):"]
    for c in caps:
        lines.append(f"- {c['name']}: {c['summary']}" if c["summary"]
                     else f"- {c['name']}")
    return "\n".join(lines)


# --- Layer 2: self-belief store (world_model shape, no set_confidence) -------

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load():
    return argo_store.load_json(SELF_PATH, [])


def _save(records):
    SELF_PATH.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(SELF_PATH, records)


def _clamp(c):
    return max(CONF_MIN, min(CONF_MAX, c))


def _beliefs(records):
    """The belief records (everything with a claim) -- excludes the SB-META marker."""
    return [r for r in records if r.get("claim")]


def _next_id(records):
    nums = []
    for r in records:
        rid = r.get("id", "")
        if rid.startswith("SB-"):
            tail = rid.split("-", 1)[1]
            if tail.isdigit():
                nums.append(int(tail))
    return f"SB-{max(nums, default=0) + 1:03d}"


def _norm_kind(kind):
    return kind if kind in _KINDS else "lesson"


def get_self_beliefs(kind=None, status=None):
    out = _beliefs(_load())
    if kind:
        out = [b for b in out if b.get("kind") == kind]
    if status:
        out = [b for b in out if b.get("status") == status]
    return out


def add_self_belief(claim, kind="lesson", confidence=SEED_CONFIDENCE, evidence=None,
                    status="unverified", source="chat"):
    """Create a self-belief at the low seed confidence (it must be earned up via
    add_evidence). Idempotent: an identical claim returns the existing id."""
    claim = (claim or "").strip()
    if not claim:
        return None
    records = _load()
    existing = next((b for b in _beliefs(records) if b.get("claim") == claim), None)
    if existing:
        return existing["id"]
    bid = _next_id(records)
    records.append({
        "id": bid,
        "kind": _norm_kind(kind),
        "claim": claim,
        "confidence": _clamp(confidence),
        "evidence": list(evidence or []),
        "refutations": [],
        "status": status,
        "source": source,
        "last_updated": _now(),
    })
    _save(records)
    log.info("self-belief added %s (%s): %s", bid, _norm_kind(kind), claim[:80])
    return bid


def add_evidence(belief_id, ref, supports=True):
    """The ONLY way a self-belief's confidence moves. Supporting evidence nudges it
    up; a refutation nudges it down and flags 'weakening' at the floor. Returns the
    updated belief or None."""
    records = _load()
    b = next((x for x in _beliefs(records) if x.get("id") == belief_id), None)
    if b is None:
        return None
    if supports:
        b.setdefault("evidence", []).append(ref)
        b["confidence"] = _clamp(b["confidence"] + EVIDENCE_STEP)
    else:
        b.setdefault("refutations", []).append(ref)
        b["confidence"] = _clamp(b["confidence"] - EVIDENCE_STEP)
        if b["confidence"] <= CONF_MIN + 1e-9:
            b["status"] = "weakening"
    b["last_updated"] = _now()
    _save(records)
    return b


def resolve_self_belief(belief_id, ref):
    """Mark an issue resolved and record the fix as supporting evidence. Status flips
    to 'resolved', but confidence still only moves through the evidence -- a resolved
    claim earns its number the same way as any other."""
    records = _load()
    b = next((x for x in _beliefs(records) if x.get("id") == belief_id), None)
    if b is None:
        return None
    b.setdefault("evidence", []).append(ref)
    b["confidence"] = _clamp(b["confidence"] + EVIDENCE_STEP)
    b["status"] = "resolved"
    b["last_updated"] = _now()
    _save(records)
    return b


def note_self_lesson(claim, kind="lesson", source="chat"):
    """Convenience used by the MCP tool: record a durable lesson about Argo itself."""
    return add_self_belief(claim, kind=kind, source=source)


def seed_identity(user_name):
    """Seed immutable identity facts on first run if none exist yet.
    Called from the webhook on every system-prompt build; no-op after first run."""
    if get_self_beliefs(kind="identity"):
        return
    for claim in [
        f"My name is Argo. I run as a Telegram bot for {user_name}.",
        "My voice is plain text, sharp opinions, no markdown -- that is how I write.",
        ("If I see a Telegram screenshot with 'Argo' as one of the participants, "
         "those are my own messages."),
    ]:
        add_self_belief(claim, kind="identity", confidence=0.92)


def format_self_for_prompt(limit=8):
    """Compact, highest-confidence-first self-belief list (plain text). "" if empty
    so callers can skip the section cleanly."""
    beliefs = sorted(_beliefs(_load()), key=lambda b: b.get("confidence", 0),
                     reverse=True)
    if not beliefs:
        return ""
    lines = []
    for b in beliefs[:limit]:
        lines.append(f"{b['id']} [{b['confidence']:.2f} {b.get('status', '')}] "
                     f"{b.get('kind', '')}: {b['claim']}")
    return "\n".join(lines)


# --- Layer 3: performance tracking + reflection (the loop) ------------------

def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def gather_performance(recent_n=RECENT_N):
    """Pure, no-network aggregation of the outcome data already on disk: project
    energy ratings (mean, recent mean, trend) and a coarse tripwire seen/settled
    count. The tripwire numbers are coarse on purpose -- the seen-store can't cleanly
    tell an alerted item from a skipped one after both settle."""
    projects = argo_store.load_json(PROJECTS_LOG, [])
    if not isinstance(projects, list):
        projects = []
    rated = [p for p in projects if isinstance(p.get("energy"), (int, float))]
    energies = [p["energy"] for p in rated]
    recent, prior = energies[-recent_n:], energies[:-recent_n]
    trend = (round(_mean(recent) - _mean(prior), 2)
             if recent and prior else None)

    seen = argo_store.load_json(SEEN_PATH, {})
    if isinstance(seen, dict):
        seen_total = len(seen)
        settled = sum(1 for v in seen.values()
                      if isinstance(v, (int, float)) and v >= SETTLED_ATTEMPTS)
    else:  # legacy list form
        seen_total = len(seen) if isinstance(seen, list) else 0
        settled = 0

    return {
        "projects_total": len(projects),
        "projects_rated": len(rated),
        "mean_energy": _mean(energies),
        "recent_mean_energy": _mean(recent),
        "energy_trend": trend,
        "recent": [{"id": p.get("id"), "energy": p.get("energy"),
                    "date": p.get("date")} for p in rated[-recent_n:]],
        "tripwire_seen": seen_total,
        "tripwire_settled": settled,
    }


def _get_meta(records):
    return next((r for r in records if r.get("id") == _META_ID), None)


def _set_meta(rated_count):
    records = _load()
    meta = _get_meta(records)
    if meta is None:
        meta = {"id": _META_ID}
        records.append(meta)
    meta["rated_count"] = rated_count
    meta["last_reflected"] = _now()
    _save(records)


_REFLECT_PROMPT = (
    "Here is your recent performance as Argo, and what you already believe about "
    "yourself.\n\n"
    "PERFORMANCE (energy is the user's 1-10 rating of how much they wanted to build "
    "each project; the tripwire counts are coarse -- the seen-store cannot cleanly "
    "separate alerted items from skipped ones):\n{stats}\n\n"
    "WHAT YOU ALREADY BELIEVE ABOUT YOURSELF:\n{beliefs}\n\n"
    "Write AT MOST two honest, specific lessons about how YOU are doing, and only if "
    "the data actually supports them (a real downward energy trend, not noise). Each "
    "lesson: one plain-text sentence, no markdown, no em dashes, concrete and about "
    "something you could change (your projects, your taste extraction, your "
    "tripwire). Do not repeat a belief you already hold. If the data does not support "
    "a real lesson, reply with the single word NONE."
)


def _parse_lessons(raw):
    lessons = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        line = line.replace("—", "-").replace("–", "-")
        if not line or line.upper() == "NONE":
            continue
        lessons.append(line)
    return lessons[:2]


def _reflect_lessons(stats, current_beliefs):
    """One short, guarded model call -> a list of lesson claim strings (<=2). Routes
    through argo_observe so the daily budget + circuit breaker apply. [] on no model
    or any failure (reflection must never crash the scheduler)."""
    import argo_observe as observe
    model = next(
        (m for m in [(os.environ.get("ARGO_CHAT_MODEL") or "claude-sonnet-4-6")]
         + observe.resolve_models()
         if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])),
        None,
    )
    if model is None:
        log.warning("reflect: no model available -- no lessons this run")
        return []
    prompt = _REFLECT_PROMPT.format(stats=json.dumps(stats, indent=2),
                                    beliefs=current_beliefs or "(none yet)")
    try:
        if observe.provider_for(model)["name"] == "anthropic":
            raw = observe.chat_with_mcp(
                "You are Argo, reflecting honestly on your own performance.",
                [{"role": "user", "content": prompt}], model, temperature=0.2,
            )
        else:
            raw = observe.generate_observations(prompt, model, temperature=0.2)
    except Exception:
        log.error("reflect: model call failed", exc_info=True)
        return []
    return _parse_lessons(raw)


def reflect(force=False):
    """The reflection loop. Free aggregation first; a model call only when there are
    >= REFLECT_MIN_NEW newly-rated projects since the last reflection (or force=True).
    New lessons land in the self-belief store as source='reflection' (idempotent).
    Returns a summary dict; never raises out to the scheduler."""
    stats = gather_performance()
    meta = _get_meta(_load())
    last_count = meta.get("rated_count", 0) if meta else 0
    new_rated = stats["projects_rated"] - last_count

    if not force and new_rated < REFLECT_MIN_NEW:
        log.info("reflect: %d new rated projects since last (<%d) -- skipping model call",
                 new_rated, REFLECT_MIN_NEW)
        return {"skipped": True, "stats": stats, "new_lessons": []}

    lessons = _reflect_lessons(stats, format_self_for_prompt(limit=12))
    new_ids = [add_self_belief(c, kind="lesson", source="reflection") for c in lessons]
    new_ids = [i for i in new_ids if i]
    _set_meta(stats["projects_rated"])
    log.info("reflect: recorded %d lesson(s) over %d rated projects",
             len(new_ids), stats["projects_rated"])
    return {"skipped": False, "stats": stats, "new_lessons": new_ids}


def reflect_cli():
    """Scheduler entrypoint: argo_scheduled COMMANDS maps the 'reflect' command here."""
    result = reflect(force=False)
    if result.get("skipped"):
        print("Reflection skipped: not enough new ratings since last run.")
    else:
        print(f"Reflection: {len(result['new_lessons'])} new lesson(s) "
              f"{result['new_lessons']}.")
    return result


if __name__ == "__main__":
    reflect_cli()
