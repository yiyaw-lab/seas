"""
Rehearse — the debate/simulation gate between a project plan and the build.

Argo plans projects (generate -> recommend -> SELECT -> scaffold). But until now
the moment a project was SELECTed it went straight to a kickoff plan: nothing
argued with the bet before it became the thing to build. Rehearse is the gate the
Seasar vision names -- "the factory that argues with itself before it ships."

Given a logged project, Rehearse:
  1. red-teams it with THREE distinct adversaries, in parallel (Sonnet each):
       - a critic  : the strongest objections (wrong / done / won't matter)
       - a user-sim : would anyone actually use, cite, or share the artifact?
       - an ops-sim : run the build forward -- what breaks, what the demo looks
                      like when it goes wrong
  2. a JUDGE (Opus) weighs the project against all three critiques and emits a
     verdict (SHIP / REVISE / KILL), says how the plan answers each surviving
     objection, and -- on SHIP/REVISE -- writes the hardened plan with explicit,
     dated KILL-CRITERIA and concrete BUILD STEPS.
  3. the result is assembled into a build-ready blueprint at
     argo/rehearsals/P-NNN.md (a strict superset of the old scaffold plan).

A bet must SURVIVE the debate to earn a blueprint, the same way a SEAS draft must
pass the emission gate to become a finding (seas_schema). A KILL verdict writes no
build plan -- it returns the reason so the bet can be fixed or dropped.

Reuses, rather than duplicates:
  - argo_observe: provider routing, model call, the DailyBudget + CircuitBreaker
    guards (every call here goes through them automatically);
  - profile: the user's name/pronouns for the prompts (no hardcoded identity);
  - the Sonnet-default / Opus-premium split from argo_webhook (adversaries are
    cheap and run on Sonnet; only the judge escalates to Opus).

Run:  python src/argo_rehearse.py P-001            (rehearse + print blueprint)
      python src/argo_rehearse.py P-001 --no-send  (same; never sends to Telegram)
"""

import os
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import argo_observe as observe
import argo_paths
import argo_predictions
import argo_store
import profile
import world_model
from argo_log import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_LOG = argo_paths.PROJECTS_LOG  # single source of truth (see argo_paths)
REHEARSAL_DIR = ROOT / "argo" / "rehearsals"

# Sonnet for the three adversaries (cheap, parallel), Opus for the single judge
# (the high-stakes synthesis). Same env knobs as argo_webhook so a deploy tunes
# both in one place.
# `or` not `.get(k, default)`: a set-but-empty CI var would otherwise win as "" and
# defeat the default, leaving an unroutable model name (provider_for("") is None).
# Per-role adversary models: genuinely DIFFERENT minds (different training, different
# blind spots) make the stress test real instead of one model arguing with itself.
# Defaults span the three providers; each is env-overridable. Graceful degradation
# (when a key is missing) is handled by _assign_adversary_models. Judge stays premium.
# `or` not `.get(k, default)`: a set-but-empty CI var would win as "" and defeat the
# default, leaving an unroutable model name (provider_for("") is None).
ADVERSARY_PREFERRED = {
    "critic": os.environ.get("ARGO_REHEARSE_CRITIC_MODEL") or "claude-sonnet-4-6",
    "user": os.environ.get("ARGO_REHEARSE_USER_MODEL") or "gpt-5",
    "ops": os.environ.get("ARGO_REHEARSE_OPS_MODEL") or "grok-4.3",
}
JUDGE_MODEL = os.environ.get("ARGO_CHAT_MODEL_PREMIUM") or "claude-opus-4-8"

# Append-only transcript of every model turn (each adversary + the judge), one
# JSON object per line. The point: a rehearsal makes ~4 paid model calls, and we
# never want to pay for tokens we then throw away -- a turn is logged the INSTANT
# it returns, so even if a later turn fails, the earlier responses are on disk for
# eval / research / replay. Gitignored (can grow large, may quote sources); on
# Railway point ARGO_REHEARSE_LOG at the mounted volume so it survives redeploys
# (mirrors ARGO_CHAT_LOG). Full capture: prompt + response + model + timestamps.
TRANSCRIPT_PATH = Path(
    os.environ.get("ARGO_REHEARSE_LOG", str(REHEARSAL_DIR / "transcripts.jsonl"))
)

# Grounding the verdict in the belief graph (the #1 cofounder move): a SHIP/REVISE
# verdict is bound to a dated, falsifiable prediction (argo_predictions) tied to a
# per-verdict-class belief (world_model), so reality grades whether Argo's build
# decision was right -- the same earned-confidence discipline a SEAS finding uses.
# The clock starts at SELECT (the user committing is the merge-equivalent); a human
# SHIPPED/DROPPED reply is the outcome; the daily score_due run moves the belief
# +-0.20. A KILL records nothing (and voids any prior prediction): the gate refused
# the bet, so there is no ship to grade. The horizon is the GRADING DELAY (when
# score_due checks), not a hard cutoff: the metric is shipped-vs-dropped. Env-tunable.
SHIP_HORIZON_DAYS = int(os.environ.get("ARGO_SHIP_HORIZON_DAYS", "14"))
_VERDICT_BELIEF = {
    "SHIP": "When Argo rehearses a bet to a SHIP verdict, the bet ships.",
    "REVISE": "When Argo rehearses a bet to a REVISE verdict, the bet ships.",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(record):
    """Append one record as a JSON line. Best-effort: a logging failure must never
    sink a rehearsal (the model call already cost credit), so it swallows errors
    after trying to surface them."""
    try:
        TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRANSCRIPT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[rehearse] transcript append failed ({type(exc).__name__}: {exc})")


def _log_turn(run_id, project_id, role, model, system, prompt, response,
              started_at, ok=True, error=None):
    """Persist one model turn (an adversary or the judge) immediately after it
    returns -- full prompt + response + metadata, so no paid output is ever lost."""
    _append_jsonl({
        "kind": "turn",
        "run_id": run_id,
        "project_id": project_id,
        "role": role,
        "model": model,
        "ok": ok,
        "error": error,
        "started_at": started_at,
        "ended_at": _now(),
        "system": system,
        "prompt": prompt,
        "response": response,
        "response_chars": len(response or ""),
    })


def _log_run(run_id, project_id, verdict, blueprint_path, models):
    """Persist the per-run summary record after all turns, so a run is queryable
    without reassembling its turns. Written even on KILL/ERROR."""
    _append_jsonl({
        "kind": "run",
        "run_id": run_id,
        "project_id": project_id,
        "verdict": verdict,
        "blueprint_path": (str(blueprint_path.relative_to(ROOT))
                           if blueprint_path else None),
        "models": models,
        "ended_at": _now(),
    })


# ---------------------------------------------------------------------------
# Model call: one helper that routes Anthropic vs OpenAI exactly like the
# existing tools (_scaffold_plan / recommend_project), so keys + guards behave
# identically. Returns the text, or raises (the caller decides what to do).
# ---------------------------------------------------------------------------

def _runnable(preferred):
    """The first model with an available key: the preferred one, else whatever
    resolve_models() yields. None if no provider key is set at all."""
    for m in [preferred] + observe.resolve_models():
        p = observe.provider_for(m)
        if p and os.environ.get(p["key_env"]):
            return m
    return None


def _assign_adversary_models(roles):
    """Assign each adversary role a model, preferring genuinely DISTINCT providers so
    the panel is diverse minds, not one model thrice. Graceful degradation: a role
    whose preferred model has no key takes another available model not yet used (to
    preserve diversity); only when no fresh provider remains do roles share a model
    (logged as degraded). Returns {role: model}, or None if NO provider key is set.

    With 3 keys -> 3 distinct models; 2 keys -> 2 distinct (one role doubles up);
    1 key -> all share, logged. Never crashes, never silently collapses."""
    # Runnable models in preference order: each role's preferred first, then
    # resolve_models() as backstop, deduped while preserving order.
    available = []
    for m in list(ADVERSARY_PREFERRED.values()) + observe.resolve_models():
        p = observe.provider_for(m)
        if p and os.environ.get(p["key_env"]) and m not in available:
            available.append(m)
    if not available:
        return None

    assigned, used = {}, set()
    for role in roles:
        pref = ADVERSARY_PREFERRED.get(role)
        pick = pref if (pref and pref in available) else None
        if pick is None or pick in used:
            fresh = next((m for m in available if m not in used), None)
            pick = fresh if fresh is not None else (pick or available[0])
        assigned[role] = pick
        used.add(pick)

    distinct = len(set(assigned.values()))
    if distinct < len(roles):
        log.info("rehearse adversaries degraded to %d distinct model(s): %s",
                 distinct, assigned)
    else:
        log.info("rehearse adversaries: %s", assigned)
    return assigned


def _call(system, prompt, model, temperature, max_tokens=1024):
    """Send one prompt to `model`, routing to its provider. Anthropic models use
    chat_with_mcp (no tools here, just the guarded messages call); others use
    generate_observations. Mirrors the branch in _scaffold_plan.

    `temperature` may be None to take the model's default -- some newer models
    (e.g. claude-opus-4-8, the judge) reject the `temperature` param outright.
    The Anthropic path omits it when None; the OpenAI fallback, which still
    accepts it, substitutes a low default. `max_tokens` is raised for the judge,
    which emits the whole hardened plan and overran the 1024 default."""
    provider = observe.provider_for(model)
    if provider is None:
        raise ValueError(f"no provider found for model {model!r}")
    if provider["name"] == "anthropic":
        return observe.chat_with_mcp(
            system, [{"role": "user", "content": prompt}], model,
            max_tokens=max_tokens, temperature=temperature,
        )
    return observe.generate_observations(
        prompt, model, temperature=0.2 if temperature is None else temperature)


# ---------------------------------------------------------------------------
# Stage 1 — the three adversaries. Each is a sharp, single-lens critique.
# ---------------------------------------------------------------------------

ADVERSARY_SYSTEM = (
    "You are an adversary stress-testing a project bet before anyone builds it. "
    "You play ONE role only, ruthlessly and concretely. Plain text, no markdown, "
    "no em dashes. Be specific: name the actual failure, not a generic caution. "
    "Short -- the sharpest version, not an essay."
)

ADVERSARIES = {
    "critic": (
        "ROLE: red-team critic. Give the strongest objections to this bet. Why "
        "might the insight be wrong, already done by someone else, or simply not "
        "matter even if it works? Attack the IDEA, not the typos. List the 2-3 "
        "objections that would actually sink it, hardest first."
    ),
    "user": (
        "ROLE: skeptical user. You are the person this artifact is supposedly "
        "for. Would you actually use it, cite it, or share it? Where does it die "
        "on contact with your real attention -- the moment you'd bounce, the step "
        "that's too much friction, the reason you'd just keep doing what you do "
        "now? Be honest about whether anyone wants this."
    ),
    "ops": (
        "ROLE: ops / failure simulator. Run the build forward as a scenario. What "
        "breaks while building it this week -- the long pole, the dependency that "
        "won't install, the dataset that isn't there, the eval with no ground "
        "truth? And when it's demoed, what does it look like when it goes WRONG? "
        "Name the 2-3 concrete breakages most likely to actually happen."
    ),
}


def _adversary_prompt(role_instructions, project_text):
    return f"{role_instructions}\n\nTHE BET:\n{project_text}\n"


def run_adversaries(project_text, run_id="", project_id="", assigned=None):
    """Run all three adversaries concurrently (wall-clock ~= one call) and return
    {role: critique_text}, each on its assigned model (different minds -> diverse
    critiques). A role that errors returns a short note instead of sinking the whole
    rehearsal -- the judge can still work with two critiques. Each turn is logged to
    the transcript the moment it returns (success OR failure), so a paid response is
    never lost even if another role or the judge later fails. `assigned` is a
    {role: model} map (computed via _assign_adversary_models if not given)."""
    if assigned is None:
        assigned = _assign_adversary_models(ADVERSARIES)
    if assigned is None:
        return None  # no key; caller reports

    def one(item):
        role, instr = item
        model = assigned[role]
        prompt = _adversary_prompt(instr, project_text)
        started = _now()
        try:
            text = _call(ADVERSARY_SYSTEM, prompt, model, temperature=0.4).strip()
            _log_turn(run_id, project_id, role, model, ADVERSARY_SYSTEM, prompt,
                      text, started, ok=True)
            return role, text
        except Exception as exc:
            note = f"(the {role} adversary could not run: {type(exc).__name__})"
            _log_turn(run_id, project_id, role, model, ADVERSARY_SYSTEM, prompt,
                      note, started, ok=False, error=f"{type(exc).__name__}: {exc}")
            return role, note

    with ThreadPoolExecutor(max_workers=len(ADVERSARIES)) as pool:
        results = dict(pool.map(one, ADVERSARIES.items()))
    return results


# ---------------------------------------------------------------------------
# Stage 2 — the judge. Weighs the bet against the critiques, issues a verdict,
# and (on SHIP/REVISE) writes the hardened plan with kill-criteria + build steps.
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are Argo, the judge in a debate about whether a project bet is worth "
    "building. You have the bet and three adversarial critiques. Your job is to "
    "DECIDE, not to hedge. Plain text, no markdown headers, no em dashes."
)


def _judge_prompt(project_text, critiques):
    name = profile.name()
    poss = profile.pronoun("possessive")
    today = datetime.now().strftime("%Y-%m-%d")
    crit_block = "\n\n".join(
        f"[{role.upper()} CRITIQUE]\n{text}" for role, text in critiques.items()
    )
    return f"""You are judging this project for {name}, who has to build it.

THE BET:
{project_text}

THE ADVERSARIES SAID:
{crit_block}

Today is {today}. Weigh the bet against the critiques and produce EXACTLY these
labeled blocks, plain text, no markdown, no em dashes:

VERDICT: <one of SHIP / REVISE / KILL> - <one line: why, in robustness terms>

WHY IT HOLDS:
<for each objection that has real force, one line: state it, then how the plan
answers it OR concede it honestly as a stated risk. If an objection is fatal, say
so -- that is what a KILL is.>

(If the verdict is KILL, stop after WHY IT HOLDS. Do NOT write a build plan for a
bet that should not be built. End with one line on what would have to change for
it to be worth revisiting.)

(If SHIP or REVISE, continue with all of the following:)

THE BET, HARDENED:
<the bet as it now stands after the debate -- the one-liner plus what to build,
sharpened by what survived. If REVISE, this is the adjusted bet.>

FAILURE SCENARIOS:
<the 2-3 most likely breakages from the ops critique, each with a one-line
mitigation baked into the plan.>

KILL-CRITERIA:
<2-3 dated, mechanically checkable stop/pivot triggers, like "if X is not true by
YYYY-MM-DD, stop". Concrete and falsifiable, not vibes. Use real dates from today.>

BUILD STEPS:
<the concrete start: the repo skeleton to create (folders/files, one line each);
the first 2-3 commands or files to write; and the single first thing to build that
proves the core idea. Tight and doable in a week. This is what {poss} hands a
coding agent.>
"""


def run_judge(project_text, critiques, run_id="", project_id=""):
    """Return (verdict, judge_text) or (None, error_message). The judge turn is
    logged to the transcript on success or failure, so its (expensive Opus) output
    is never lost."""
    model = _runnable(JUDGE_MODEL)
    if model is None:
        return None, "No model available to judge the rehearsal."
    prompt = _judge_prompt(project_text, critiques)
    started = _now()
    try:
        # temperature=None: the judge runs on Opus, which rejects the param.
        # max_tokens raised: the judge emits the full hardened plan (verdict +
        # why-it-holds + failure scenarios + kill-criteria + build steps).
        text = _call(JUDGE_SYSTEM, prompt, model,
                     temperature=None, max_tokens=3000).strip()
    except Exception as exc:
        _log_turn(run_id, project_id, "judge", model, JUDGE_SYSTEM, prompt, "",
                  started, ok=False, error=f"{type(exc).__name__}: {exc}")
        return None, f"The judge could not run ({type(exc).__name__}: {exc})."
    verdict = _parse_verdict(text)
    _log_turn(run_id, project_id, "judge", model, JUDGE_SYSTEM, prompt, text,
              started, ok=True)
    return verdict, text


def _parse_verdict(judge_text):
    """Pull SHIP / REVISE / KILL out of the VERDICT line. Defaults to REVISE if the
    label is malformed -- a cautious middle that still writes a plan rather than
    silently shipping or killing."""
    for line in judge_text.splitlines():
        up = line.strip().upper()
        if up.startswith("VERDICT:"):
            for v in ("KILL", "REVISE", "SHIP"):
                if v in up:
                    return v
    return "REVISE"


# ---------------------------------------------------------------------------
# Stage 3 — assemble + persist the blueprint, and a terse Telegram summary.
# ---------------------------------------------------------------------------

def _blueprint_doc(project_id, verdict, project_text, critiques, judge_text):
    today = datetime.now().strftime("%Y-%m-%d")
    crit_block = "\n\n".join(
        f"## {role.upper()} said\n{text}" for role, text in critiques.items()
    )
    return f"""BLUEPRINT {project_id}  (rehearsed {today}, verdict: {verdict})

# The original bet
{project_text}

# The judgment
{judge_text}

# The full debate (raw critiques)
{crit_block}
"""


def _write_blueprint(project_id, doc):
    REHEARSAL_DIR.mkdir(parents=True, exist_ok=True)
    path = REHEARSAL_DIR / f"{project_id}.md"
    path.write_text(doc)
    return path


def _summary_line(project_id, verdict, judge_text):
    """A terse, Telegram-friendly verdict + the single biggest surviving risk.
    Plain text, no em dashes (Argo voice). The biggest risk is the first line of
    WHY IT HOLDS, which the judge ordered hardest-first."""
    risk = ""
    lines = judge_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("WHY IT HOLDS"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    risk = nxt.strip().lstrip("-").strip()
                    break
            break
    verb = {"SHIP": "It holds up", "REVISE": "It needs a tweak",
            "KILL": "I'd drop this one"}.get(verdict, "Rehearsed it")
    out = f"Rehearsed {project_id}. {verb} ({verdict})."
    if risk:
        out += f" Biggest thing to watch: {risk}"
    return out


def _record_judgment_prediction(entry, verdict):
    """Bind a SHIP/REVISE verdict to a dated, scorable prediction so reality grades
    the build decision. Mutates `entry` (sets judgment_prediction_id + judgment_verdict)
    -- the caller persists it. Idempotent per project while the verdict is unchanged;
    a re-rehearsal that CHANGES the verdict (SHIP<->REVISE, or anything->KILL) voids the
    old prediction first, so a bet always grades the verdict-class it currently holds
    and a KILL grades nothing. Arms immediately when the project is already SELECTed
    (the SELECT path rehearses AFTER marking selected); otherwise SELECT arms it.
    Best-effort: a store error must never sink the rehearsal (it already happened).
    Returns the prediction id or None."""
    pid = entry.get("id")
    existing = entry.get("judgment_prediction_id")
    # A verdict change retires the old prediction: it grades the wrong (or, for KILL,
    # a now-refused) verdict-class belief. Void it before recording/returning.
    if existing and entry.get("judgment_verdict") != verdict:
        voided = False
        try:
            argo_predictions.cancel(
                existing, f"re-rehearsed {entry.get('judgment_verdict')} -> {verdict}")
            voided = True
        except Exception:
            log.warning("rehearse: could not void prediction for %s", pid, exc_info=True)
        if voided:
            entry.pop("judgment_prediction_id", None)
            entry.pop("judgment_verdict", None)
            existing = None
        # If the void FAILED (store error), keep the OLD prediction bound rather than
        # orphaning a still-live armed pred AND recording a duplicate: two predictions
        # with the same project_shipped metric would both grade one SHIPPED, double-
        # counting the outcome across two beliefs. Reusing the stale binding degrades
        # to at-worst the wrong verdict-class, not a double move.
    if verdict not in _VERDICT_BELIEF:
        return None  # KILL/unknown: the gate refused the bet, nothing to grade
    try:
        pred_id = existing
        if not pred_id:
            belief_id = world_model.add_belief(_VERDICT_BELIEF[verdict],
                                               status="unverified")
            pred_id = argo_predictions.record(
                belief_id,
                f"{pid}, rehearsed {verdict}, gets built and shipped rather than "
                f"dropped (graded {SHIP_HORIZON_DAYS} days after selection).",
                {"kind": "project_shipped", "project_id": pid},
                SHIP_HORIZON_DAYS,
                source="rehearse",
            )
            entry["judgment_prediction_id"] = pred_id
            entry["judgment_verdict"] = verdict
        if entry.get("selected"):
            argo_predictions.arm(pred_id)
        return pred_id
    except Exception:
        # Best-effort: grounding the verdict must never sink the rehearsal (the
        # debate already happened and the blueprint is written). Broad net, logged.
        log.warning("rehearse: judgment prediction not recorded for %s",
                    pid, exc_info=True)
        return None


def _stamp_project(project_id, verdict, blueprint_path):
    """Record the rehearsal result on the project entry, mirroring how the webhook
    stamps selected/selected_at. Additive fields; existing readers ignore them.
    Also grounds the verdict in the belief graph (the build-decision loop).
    Best-effort: never raises (the rehearsal already happened)."""
    projects = argo_store.load_json(PROJECTS_LOG, None)
    if not isinstance(projects, list):
        return
    for entry in projects:
        if entry.get("id") == project_id:
            entry["rehearsed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry["verdict"] = verdict
            entry["blueprint_path"] = str(blueprint_path.relative_to(ROOT))
            # Ground the verdict: record/arm the belief-bound prediction. Mutates
            # entry with judgment_prediction_id; the save below persists it.
            _record_judgment_prediction(entry, verdict)
            break
    try:
        argo_store.save_json(PROJECTS_LOG, projects)
    except OSError:
        pass


def _load_project(project_id):
    import json
    if not PROJECTS_LOG.exists():
        return None
    try:
        log = json.loads(PROJECTS_LOG.read_text())
    except (ValueError, OSError):
        return None
    if not log:
        return None
    if project_id:
        return next((p for p in log if p.get("id") == project_id), None)
    return log[-1]


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

def rehearse(project_id=""):
    """Stress-test a project and return (verdict, blueprint_path, summary_text).

    verdict is SHIP / REVISE / KILL. blueprint_path is the written argo/rehearsals
    doc (None on KILL or on failure -- a bad bet earns no build plan). summary_text
    is the terse Telegram line. On any setup failure (no project, no key) returns
    ("ERROR", None, <reason>) so the caller can relay it honestly."""
    entry = _load_project(project_id)
    if entry is None:
        return "ERROR", None, (f"Couldn't find {project_id} to rehearse."
                               if project_id else "No project to rehearse yet.")
    project_text = entry.get("text", "").strip()
    pid = entry.get("id", project_id or "P-???")
    if not project_text:
        return "ERROR", None, f"{pid} has no project text to rehearse."

    # One id ties every turn of this rehearsal together in the transcript, so a
    # re-run of the same project stays distinguishable (the blueprint .md is
    # overwritten, but the transcript is append-only -- every debate is kept).
    run_id = f"{pid}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    assigned = _assign_adversary_models(ADVERSARIES)
    models = {"adversaries": assigned, "judge": JUDGE_MODEL}

    critiques = run_adversaries(project_text, run_id, pid, assigned)
    if critiques is None:
        _log_run(run_id, pid, "ERROR", None, models)
        return "ERROR", None, ("No model available to rehearse right now. Tell the "
                               "user plainly and suggest trying again shortly.")

    verdict, judge_text = run_judge(project_text, critiques, run_id, pid)
    if verdict is None:
        # The adversary turns are already on disk (logged as they returned), so
        # their credit isn't wasted even though the judge failed.
        _log_run(run_id, pid, "ERROR", None, models)
        return "ERROR", None, judge_text  # judge_text holds the error message

    doc = _blueprint_doc(pid, verdict, project_text, critiques, judge_text)
    summary = _summary_line(pid, verdict, judge_text)

    # A KILL writes no build plan -- the gate refusing to bless a weak bet. We
    # still persist the debate doc (so the reasoning is on record) but return no
    # blueprint path, so the caller routes it back into the loop instead of
    # handing over build steps.
    path = _write_blueprint(pid, doc)
    _stamp_project(pid, verdict, path)
    blueprint_path = None if verdict == "KILL" else path
    _log_run(run_id, pid, verdict, path, models)
    return verdict, blueprint_path, summary


def build_steps(blueprint_path):
    """Pull just the BUILD STEPS section out of a written blueprint, for sending
    as the concrete start on a SHIP/REVISE verdict. Falls back to the whole doc if
    the section marker isn't found."""
    if blueprint_path is None:
        return ""
    try:
        doc = Path(blueprint_path).read_text()
    except OSError:
        return ""
    marker = "BUILD STEPS:"
    idx = doc.find(marker)
    if idx == -1:
        return doc
    return doc[idx + len(marker):].strip()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project_id = args[0] if args else ""

    verdict, blueprint_path, summary = rehearse(project_id)

    print("\n=== Rehearse ===\n")
    # The actual per-run assignment is logged inside rehearse(); here just show the
    # configured preference (avoids a second _assign_adversary_models call + log).
    print(f"models: adversaries(preferred)={ADVERSARY_PREFERRED}  "
          f"judge={JUDGE_MODEL}\n")
    print("SUMMARY:", summary)
    print("VERDICT:", verdict)
    if blueprint_path:
        print("BLUEPRINT:", blueprint_path.relative_to(ROOT))
        print("\n--- blueprint ---\n")
        print(blueprint_path.read_text())
    else:
        print("(no blueprint written)")


if __name__ == "__main__":
    main()
