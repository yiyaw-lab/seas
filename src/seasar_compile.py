"""
Seasar -- the Build-Order Machine.

Takes a raw idea and compiles a structured BUILD ORDER: a fleet-ready plan that N
autonomous coding agents can build in parallel WITHOUT colliding. The load-bearing
outputs are (1) a real task DAG decomposed along file boundaries so same-wave tasks
never write the same file, and (2) typed interface contracts -- the seams between
modules -- so the agents agree on the wire before any of them starts.

Four stages, streamed as SSE so the UI can watch the factory think:

  1. SMELT (cheap Sonnet) -- normalize the raw idea + dials into a crisp problem
     brief, infer a stack if blank, list assumptions.
  2. DEBATE (argo_rehearse.run_adversaries) -- three DISTINCT models (Sonnet /
     gpt-5 / grok) red-team the brief in parallel. This is the visible "factory
     that argues with itself before it ships."
  3. CAST (premium Opus) -- compile the whole BuildOrder JSON from the brief +
     dials + surviving critiques. Everything EXCEPT buildability/debate/meta.
  4. STAMP (deterministic, in Python -- NOT asked of the LLM) -- compute the
     falsifiable buildability score with teeth (collision-safety, one-writer-rate,
     parallelism, contract-coverage, testability) and validate/repair the DAG.

Reuses, never duplicates, the existing model layer:
  - argo_rehearse.run_adversaries / _assign_adversary_models / _call / _runnable /
    JUDGE_MODEL  (the debate stage + the routed, guarded, temperature-safe model call)
  - argo_observe (provider routing, budget + circuit-breaker guards ride every call)
  - argo_store.load_json / save_json (atomic JSON I/O)
  - argo_log.get_logger (operator logging; not print)

Run a real end-to-end smoke:
  PYTHONPATH=src python3 src/seasar_compile.py "an idea in quotes"
(makes real paid model calls -- prints each stage + the final score block).
"""

import io
import json
import os
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import argo_rehearse as rehearse
import argo_store
import seasar_verify
from argo_log import get_logger

# Load .env so ANTHROPIC_API_KEY / OPENAI_API_KEY / XAI_API_KEY are present before
# any model call. argo_observe already does this on import (it is imported via
# argo_rehearse), but we replicate the entrypoint pattern so this module is honest
# about its own dependency rather than relying on an import side effect.
ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

log = get_logger(__name__)

ORDERS_DIR = ROOT / "data" / "seasar_orders"

# A fast/cheap model for SMELT (the normalize step), the premium judge for CAST
# (the high-stakes compile). Both routed through _runnable so a missing key falls
# back gracefully to whatever provider is available.
SMELT_PREFERRED = os.environ.get("SEASAR_SMELT_MODEL") or "claude-sonnet-4-6"
CAST_MODEL = rehearse.JUDGE_MODEL  # premium (default claude-opus-4-8)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Defensive JSON parsing -- the model is told to return ONLY JSON, but we never
# trust that: strip ```json fences if present, then find the outermost {...}.
# ---------------------------------------------------------------------------

def _parse_json_object(text):
    """Pull a JSON OBJECT out of a model response. Tolerates ```json fences and
    leading/trailing prose by extracting the outermost brace-balanced span, and a
    top-level array that wraps the object. Always returns a dict; raises ValueError
    otherwise (the caller turns that into an honest error event -- never a fabricated
    order, never a non-dict the rest of the pipeline would choke on)."""
    if not text:
        raise ValueError("empty model response")
    s = text.strip()
    # Strip a leading ```json / ``` fence and any trailing fence.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    # Direct parse first -- but only accept a JSON object.
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        obj = None
    if isinstance(obj, dict):
        return obj
    # Fall back to the outermost brace-balanced object (handles prose/fences and a
    # top-level array wrapping the object).
    start = s.find("{")
    if start == -1:
        raise ValueError("model response was not a JSON object")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                inner = json.loads(s[start:i + 1])
                if not isinstance(inner, dict):
                    raise ValueError("model response was not a JSON object")
                return inner
    raise ValueError("unbalanced JSON object in model response")


# ---------------------------------------------------------------------------
# Stage 1 -- SMELT. Normalize the raw idea + dials into a crisp brief, infer a
# stack if blank, list 2-3 assumptions. Fast/cheap model.
# ---------------------------------------------------------------------------

_SMELT_SYSTEM = (
    "You are Seasar's intake refiner. You turn a rough product idea into a crisp, "
    "buildable problem brief. Be concrete and specific; no fluff, no marketing. "
    "Return ONLY valid JSON, no markdown fences, no prose."
)


def _smelt_prompt(idea, stack, scope, agents):
    stack_line = stack.strip() or "(left blank -- infer the best-fit stack)"
    return f"""Normalize this idea into a build brief.

RAW IDEA:
{idea}

DIALS:
- declared stack: {stack_line}
- scope: {scope} (weekend = a sharp demo; mvp = the smallest real product; product = production-grade)
- target fleet size: {agents} autonomous coding agents building in parallel

Return ONLY this JSON object:
{{
  "normalized_idea": "a few crisp sentences: the problem, the user, what the thing does",
  "inferred_stack": "one line: the concrete stack to build it in (honor the declared stack if given, else infer the simplest good fit)",
  "assumptions": ["2-3 assumptions you are making to make this buildable", "..."]
}}"""


def smelt(idea, stack, scope, agents):
    """Run the SMELT call. Returns ({normalized_idea, inferred_stack, assumptions},
    model, raw_text) -- the raw text is kept for internal cost metering. Raises on
    no-model or parse failure (the generator turns it into an error)."""
    model = rehearse._runnable(SMELT_PREFERRED)
    if model is None:
        raise RuntimeError("no model available for SMELT (no provider key set)")
    text = rehearse._call(_SMELT_SYSTEM, _smelt_prompt(idea, stack, scope, agents),
                          model, temperature=0.3, max_tokens=1200)
    data = _parse_json_object(text)
    return {
        "normalized_idea": str(data.get("normalized_idea", idea)).strip(),
        "inferred_stack": str(data.get("inferred_stack", stack or "")).strip(),
        "assumptions": [str(a).strip() for a in data.get("assumptions", []) if str(a).strip()],
    }, model, text


# ---------------------------------------------------------------------------
# Stage 3 -- CAST. The premium compile. Produces the whole BuildOrder EXCEPT
# buildability (Stamp), debate (run_adversaries), and meta (we set it).
# ---------------------------------------------------------------------------

_CAST_SYSTEM = (
    "You are Seasar's build-order compiler. Given an idea, a normalized brief, the "
    "user's dials, and three adversarial critiques, produce the world's most "
    "BUILDABLE plan for N autonomous coding agents to build in parallel WITHOUT "
    "colliding. The load-bearing parts are the task DAG (decomposed along file "
    "boundaries so same-wave tasks never share a file) and the interface contracts "
    "(typed seams between modules). Bake the surviving critiques into the spec, "
    "constitution, and quality_gates, AND record in `hardening` how each surviving "
    "objection is answered (or honestly conceded as a stated risk). List in "
    "`provisions` every human-in-the-loop input (secrets, accounts, configs, infra, "
    "data, decisions) so they're collected UP FRONT and the fleet never stalls "
    "mid-build. The order must be EXECUTABLE DNA, not prose: every contract carries "
    "literal compilable `source` (the file other tasks IMPORT, not a description of "
    "it); a wave-0 `fixtures` set carries the literal golden corpus every test runs "
    "against; `scaffold_files` carry the literal runnable boot skeleton; and "
    "`orchestration.handoff_protocol` + `contract_evolution` plus each work order's "
    "`definition_of_done` make the merge protocol explicit. The buildability score "
    "grades EXECUTABILITY, so prose-only contracts and vapor fixtures score F. "
    "Return ONLY the JSON described, no prose, no markdown fences."
)

# The schema handed to the model. Mirrors the contract exactly; buildability,
# debate, and meta are filled in by us, so they are omitted from what we ask for.
_CAST_SCHEMA = """{
  "title": "short product name derived from the idea",
  "tagline": "one punchy line",
  "stack": "the chosen/inferred stack, one line",
  "scope": "weekend | mvp | product",
  "agent_count": <int, the target fleet size>,
  "constitution": ["non-negotiable invariant", "..."],
  "spec": {
    "what": "...", "why": "...",
    "acceptance_criteria": ["EARS-style: WHEN <trigger> THE SYSTEM SHALL <response>", "..."],
    "non_goals": ["explicitly out of scope", "..."],
    "examples": [{ "input": "concrete input", "output": "concrete expected output" }]
  },
  "repo_scaffold": [{ "path": "src/api/server.ts", "purpose": "one line" }],
  "contracts": [{ "name": "api-spec", "kind": "openapi|schema|types|data-model|event", "owner_task": "T2", "detail": "the human rationale for this seam", "source_lang": "typescript|zod|sql|json-schema|openapi|graphql|protobuf|python", "source_path": "src/lib/contracts/api.ts", "source": "the LITERAL compilable contract source -- the file dependent tasks IMPORT byte-for-byte, not a description of it", "version": "semver; bump on a breaking change so consumers re-verify (default 1.0.0)" }],
  "tasks": [{
    "id": "T1", "title": "...", "wave": 1, "depends_on": [],
    "files": ["src/api/server.ts"], "agent_role": "Backend|Frontend|Schema|Infra|Tests",
    "test": "the test that proves this task done", "acceptance": "boolean exit gate, checkable"
  }],
  "work_orders": [{ "agent": "Agent A", "role": "Backend", "task_ids": ["T1","T3"], "worktree": "wt/agent-a", "brief": "the scoped brief for this worker", "definition_of_done": "what 'done' means for this agent + the handoff artifact it leaves (e.g. contract committed, typecheck green) before a dependent agent starts" }],
  "quality_gates": [{ "name": "anchor-drift", "threshold": "the measurable bar, in words", "blocks_merge": true, "test_lang": "typescript|python|...", "test_path": "tests/gates/anchor-drift.test.ts", "test_source": "the LITERAL runnable test (authored by YOU, the compiler) that asserts the threshold against named fixture_refs -- the feature agent INHERITS it, never writes its own", "fixture_refs": ["tests/fixtures/sample.epub"] }],
  "orchestration": { "topology": "orchestrator-worker", "waves": [["T1","T2"],["T3"]], "consistency_check": "how spec<->tasks<->contracts are checked to agree before any agent starts", "handoff_protocol": "each agent's definition-of-done + the integration/merge order + how a downstream agent requests a change to a contract it does NOT own (propose-to-owner, never edit the file)", "contract_evolution": "the exact ritual to change a frozen contract mid-build without two agents writing the same file" },
  "fixtures": [{ "path": "tests/fixtures/sample.epub", "purpose": "golden input every anchor/export test runs against", "format": "epub|pdf|json|csv|sql|md", "body": "the LITERAL fixture content for text fixtures; empty when binary", "binary": false, "generator": "for a binary fixture (epub/pdf/image): the literal script/command that reproducibly PRODUCES it", "produced_by_task": "T0", "consumed_by_tasks": ["T6","T7"] }],
  "scaffold_files": [{ "path": "package.json", "purpose": "the runnable boot skeleton, present before any feature task", "body": "the LITERAL file content -- real JSON/TS/YAML that installs, typechecks, lints, and runs an empty test green" }],
  "decisions": [{ "id": "D1", "question": "a real ambiguity the spec does NOT settle that an implementing agent would otherwise resolve SILENTLY (e.g. may a confidential record be exported?)", "anchor_task": "T7", "anchor_file": "src/lib/export/policy.ts", "options": ["allow", "deny"], "recommended": "deny", "rationale": "why it matters + why the default" }],
  "hardening": [{ "concern": "a red-team objection in plain terms", "source": "critic|user|ops", "resolution": "concretely how THIS plan answers it -- cite the spec rule, quality gate, task, or contract that addresses it -- or an honest concession kept as a stated risk with its mitigation" }],
  "provisions": [{ "name": "OpenAI API key", "kind": "secret|account|config|infra|data|decision", "env_var": "OPENAI_API_KEY (the var agents read it from; empty if not an env var)", "needed_by": ["T2"], "how_to_get": "concrete steps or URL to obtain/decide it", "blocking": true, "recommended": "for a config/decision: the default the build proceeds on; empty for a secret" }]
}"""


def _cast_prompt(idea, brief, scope, agents, critiques):
    crit_block = "\n\n".join(
        f"[{role.upper()} CRITIQUE]\n{text}" for role, text in critiques.items()
    )
    return f"""Compile a BUILD ORDER.

THE ORIGINAL IDEA:
{idea}

THE NORMALIZED BRIEF:
{brief['normalized_idea']}

INFERRED/DECLARED STACK: {brief['inferred_stack']}
ASSUMPTIONS: {"; ".join(brief['assumptions']) or "(none)"}

DIALS:
- scope: {scope}
- fleet size: {agents} autonomous coding agents building in parallel

THE ADVERSARIES SAID (bake the surviving objections into spec/constitution/quality_gates):
{crit_block}

HARD RULES FOR THE PLAN:
- tasks form a real DAG. `wave` is 1-based; tasks in the SAME wave run in PARALLEL,
  so they must NEVER list the same file in `files`. `depends_on` may reference only
  EARLIER tasks (a lower wave).
- `files` is the explicit set of paths each task WRITES. Aim for one-writer-per-file
  across the whole plan so parallel agents never collide.
- every contract is a typed seam between modules; set its `owner_task` to the task
  that defines it. Anything other tasks depend on should expose a contract.
- `orchestration.waves` must list every task id EXACTLY ONCE, grouped by wave.
- `agent_count` and the number of work_orders should match the fleet size ({agents}).
- distribute tasks across work_orders so each agent owns a coherent slice.
- `hardening` must address every objection above that has real force: state the
  concern plainly, tag its source (critic/user/ops), and give a concrete resolution
  that points at a specific spec rule, quality gate, task, or contract -- or concede
  it honestly as a stated risk with a mitigation. Do not hand-wave.
- `provisions`: enumerate EVERY human-in-the-loop input the build needs (API keys,
  third-party accounts, config values, infra like a DB/host/domain, datasets the
  human must supply, and genuine product/design decisions) so they're collected ONCE
  up front. Bias the plan toward ZERO human-in-the-loop: when a choice has a sane
  default (stack, library, DB, naming), PICK it -- record it as a config/decision
  provision with `recommended` set and `blocking:false` -- rather than leaving it for
  the human. Reserve `blocking:true` for what only a human can supply (secrets, paid
  accounts, their data, irreversible decisions). Anything agents read at runtime must
  declare its `env_var`; tasks read from env, they never prompt the human. Add a
  quality gate (or fold into the consistency_check) that all blocking provisions are
  satisfied before wave 1 starts.
- `decisions`: surface EVERY ambiguity the spec does NOT settle that an implementing
  agent would otherwise resolve SILENTLY (a confident wrong guess is the dominant
  autonomous failure). For each give the `question`, the `anchor_task`/`anchor_file`
  that faces it, the `options`, a `recommended` default, and the `rationale`. These
  become a DECISIONS.md ledger seeded with a unique `SEASAR_DECIDE_<id>` sentinel per
  decision; a generated assert-no-sentinel gate FAILS the build while any sentinel
  survives, so no decision can ship unresolved. Prefer 2-6 high-leverage decisions;
  do not invent ambiguity the spec already settles.
- `quality_gates`: for each BLOCKING gate, author the LITERAL executable test
  (`test_source` + `test_path` + `test_lang`) that operationalizes its `threshold`
  against named `fixture_refs`. YOU write the predicate so the feature agent inherits a
  gate it cannot tautologize -- a gate with only a prose `threshold` is not a gate.

Return ONLY this JSON object (no prose, no markdown fences):
{_CAST_SCHEMA}"""


def cast(idea, brief, scope, agents, critiques):
    """Run the premium CAST compile. Returns (parsed partial BuildOrder dict, model,
    raw_text) -- everything but buildability/debate/meta; the raw text is kept for
    internal cost metering. Raises on no-model or parse failure."""
    model = rehearse._runnable(CAST_MODEL)
    if model is None:
        raise RuntimeError("no model available for CAST (no provider key set)")
    # temperature=None: CAST runs on Opus (claude-opus-4-8), which 400s if given
    # a temperature. _call omits it for the Anthropic path when None.
    # max_tokens generous: the whole BuildOrder JSON (a deep DAG + contracts +
    # work orders + spec) is large, and a cap that truncates mid-JSON yields an
    # unbalanced, unparseable object. 16000 leaves real headroom over the ~12K
    # chars a full order runs.
    text = rehearse._call(_CAST_SYSTEM, _cast_prompt(idea, brief, scope, agents, critiques),
                          model, temperature=None, max_tokens=16000)
    return _parse_json_object(text), model, text


# ---------------------------------------------------------------------------
# Stage 4 -- STAMP. The falsifiable buildability score, computed deterministically
# in Python (NEVER asked of the LLM). Plus consistency validation + minimal repair.
# ---------------------------------------------------------------------------

def _grade(score):
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _as_list(v):
    """Coerce a field that should be a list. A bare string becomes a one-element
    list (the model emitted a scalar where a list belongs -- e.g. files="src/a.py");
    anything else collapses to []. Prevents a string being iterated char-by-char,
    which would silently corrupt the buildability score."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _wave_of(t):
    """Tolerant 1-based wave number. A non-numeric/garbage wave (e.g. "one") folds
    into wave 1 -- a quality signal reflected in the score, never a 500."""
    try:
        return max(1, int(t.get("wave", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _merge_order(order):
    """Deterministic topological merge order: a task merges only after the tasks it
    depends on, ties broken by (wave, id) so the order is stable. The orchestrator merges
    in this order and re-runs the gate after each merge (the merge-queue). A dependency
    cycle can't hang -- leftover tasks are appended by (wave, id)."""
    tasks = [t for t in (order.get("tasks") or []) if isinstance(t, dict) and t.get("id")]
    by_id = {t["id"]: t for t in tasks}
    deps = {t["id"]: {d for d in (t.get("depends_on") or []) if d in by_id and d != t["id"]}
            for t in tasks}
    key = lambda i: (_wave_of(by_id[i]), str(i))
    done, out = set(), []
    while True:
        ready = sorted([i for i in deps if i not in done and deps[i] <= done], key=key)
        if not ready:
            break
        done.add(ready[0])
        out.append(ready[0])
    for i in sorted(deps, key=key):
        if i not in done:
            done.add(i)
            out.append(i)   # cycle remnant -- deterministic, never a hang
    return out


def _normalize_order(order):
    """Coerce the model's raw JSON into the shape the rest of the pipeline (stamp,
    bundle) and the frontend renderers ASSUME: every required array/object present
    and correctly typed, list members that must be dicts filtered to dicts, per-task
    files/depends_on coerced to lists and wave to an int. A model that drifts off
    schema degrades to a weaker plan, never crashes a paid compile or white-screens
    the result. Mutates and returns `order`."""
    # list-of-dict fields -> list, dict members only.
    for k in ("repo_scaffold", "contracts", "tasks", "work_orders", "quality_gates",
              "hardening", "provisions", "fixtures", "scaffold_files", "decisions"):
        order[k] = [x for x in _as_list(order.get(k)) if isinstance(x, dict)]
    # hardening items: coerce the three string fields so the renderer can't crash.
    for h in order["hardening"]:
        h["concern"] = str(h.get("concern", "") or "")
        h["source"] = str(h.get("source", "") or "")
        h["resolution"] = str(h.get("resolution", "") or "")
    # provision items: the human-in-the-loop pre-flight checklist.
    for p in order["provisions"]:
        p["name"] = str(p.get("name", "") or "")
        p["kind"] = str(p.get("kind", "") or "config")
        p["env_var"] = str(p.get("env_var", "") or "")
        p["how_to_get"] = str(p.get("how_to_get", "") or "")
        p["recommended"] = str(p.get("recommended", "") or "")
        p["needed_by"] = [str(t) for t in _as_list(p.get("needed_by"))]
        p["blocking"] = bool(p.get("blocking", False))
    # contracts: the typed seam IS literal source, not prose -- coerce the new fields so
    # the score (contracts_compile) and the bundle (a real .ts/.sql file) can rely on them.
    for c in order["contracts"]:
        c["source"] = str(c.get("source", "") or "")
        c["source_lang"] = str(c.get("source_lang", "") or "")
        c["source_path"] = str(c.get("source_path", "") or "")
        c["version"] = str(c.get("version", "") or "1.0.0")
    # fixtures: the wave-0 golden corpus every test runs against.
    for f in order["fixtures"]:
        f["path"] = str(f.get("path", "") or "")
        f["purpose"] = str(f.get("purpose", "") or "")
        f["format"] = str(f.get("format", "") or "")
        f["body"] = str(f.get("body", "") or "")
        f["generator"] = str(f.get("generator", "") or "")
        f["binary"] = bool(f.get("binary", False))
        f["produced_by_task"] = str(f.get("produced_by_task", "") or "")
        f["consumed_by_tasks"] = [str(t) for t in _as_list(f.get("consumed_by_tasks"))]
    # scaffold_files: the literal runnable boot skeleton.
    for s in order["scaffold_files"]:
        s["path"] = str(s.get("path", "") or "")
        s["purpose"] = str(s.get("purpose", "") or "")
        s["body"] = str(s.get("body", "") or "")
    # work_orders: coerce task_ids to a list (a bare string would char-iterate into an
    # empty allowed-files set and a substring-inverted forbidden set in the packet) plus
    # the explicit per-agent definition-of-done / handoff artifact.
    for wo in order["work_orders"]:
        wo["task_ids"] = [str(i) for i in _as_list(wo.get("task_ids"))]
        wo["definition_of_done"] = str(wo.get("definition_of_done", "") or "")
    # decisions: the forced-stop ledger -- each is an ambiguity an agent must RESOLVE,
    # not guess. Give every decision a UNIQUE, gate-matchable id: charset-clamped to
    # [A-Za-z0-9] so the seeded SEASAR_DECIDE_<id> token matches the gate regex, and
    # deduped so two decisions never collapse to one indistinguishable sentinel.
    _seen_ids = set()
    for n, d in enumerate(order["decisions"], 1):
        rid = re.sub(r"[^A-Za-z0-9]", "", str(d.get("id", "") or "")) or f"D{n}"
        cand, k = rid, 2
        while cand in _seen_ids:
            cand, k = f"{rid}{k}", k + 1
        d["id"] = cand
        _seen_ids.add(cand)
        d["question"] = str(d.get("question", "") or "")
        d["anchor_task"] = str(d.get("anchor_task", "") or "")
        d["anchor_file"] = str(d.get("anchor_file", "") or "")
        d["options"] = [str(o) for o in _as_list(d.get("options"))]
        d["recommended"] = str(d.get("recommended", "") or "")
        d["rationale"] = str(d.get("rationale", "") or "")
    # quality_gates: each carries a compiler-authored executable predicate (test_source)
    # so the feature agent inherits a gate it cannot tautologize.
    for g in order["quality_gates"]:
        g["name"] = str(g.get("name", "") or "")
        g["threshold"] = str(g.get("threshold", "") or "")
        g["test_lang"] = str(g.get("test_lang", "") or "")
        g["test_path"] = str(g.get("test_path", "") or "")
        g["test_source"] = str(g.get("test_source", "") or "")
        g["fixture_refs"] = [str(x) for x in _as_list(g.get("fixture_refs"))]
        g["blocks_merge"] = bool(g.get("blocks_merge", False))
    # constitution -> list of non-empty strings.
    order["constitution"] = [
        str(c).strip() for c in _as_list(order.get("constitution")) if str(c).strip()
    ]
    # spec object + its arrays.
    spec = order.get("spec")
    spec = spec if isinstance(spec, dict) else {}
    spec["what"] = str(spec.get("what", "") or "")
    spec["why"] = str(spec.get("why", "") or "")
    spec["acceptance_criteria"] = [str(x) for x in _as_list(spec.get("acceptance_criteria"))]
    spec["non_goals"] = [str(x) for x in _as_list(spec.get("non_goals"))]
    spec["examples"] = [e for e in _as_list(spec.get("examples")) if isinstance(e, dict)]
    order["spec"] = spec
    # orchestration object.
    orch = order.get("orchestration")
    orch = orch if isinstance(orch, dict) else {}
    orch["topology"] = str(orch.get("topology", "") or "orchestrator-worker")
    orch["consistency_check"] = str(orch.get("consistency_check", "") or "")
    orch["handoff_protocol"] = str(orch.get("handoff_protocol", "") or "")
    orch["contract_evolution"] = str(orch.get("contract_evolution", "") or "")
    orch["waves"] = orch["waves"] if isinstance(orch.get("waves"), list) else []
    order["orchestration"] = orch
    # per-task coercion.
    for t in order["tasks"]:
        t["files"] = [str(f) for f in _as_list(t.get("files"))]
        t["depends_on"] = [str(d) for d in _as_list(t.get("depends_on"))]
        t["wave"] = _wave_of(t)
    # required scalars.
    order["title"] = str(order.get("title") or "Untitled Build Order")
    order["tagline"] = str(order.get("tagline") or "")
    return order


def _validate_and_repair(order):
    """Validate spec<->tasks<->contracts consistency. Repair minimally in place (drop
    dangling depends_on refs, partition waves from the tasks if orchestration.waves is
    inconsistent) and return a list of human-readable warning notes. Logs each, never
    crashes -- an inconsistent model output is a quality signal in the score, not a
    500."""
    notes = []
    tasks = order.get("tasks") or []
    task_ids = {t.get("id") for t in tasks if t.get("id")}

    # Drop dangling depends_on references (a depends_on id that no task defines).
    for t in tasks:
        deps = t.get("depends_on") or []
        kept = [d for d in deps if d in task_ids]
        if len(kept) != len(deps):
            dropped = [d for d in deps if d not in task_ids]
            notes.append(f"task {t.get('id')} dropped dangling depends_on {dropped}")
            t["depends_on"] = kept

    # orchestration.waves must partition every task id exactly once. If it doesn't,
    # rebuild it deterministically from each task's `wave`.
    orch = order.get("orchestration") or {}
    waves = orch.get("waves") or []
    flat = [tid for w in waves for tid in w]
    if sorted(flat) != sorted(task_ids):
        rebuilt = {}
        for t in tasks:
            rebuilt.setdefault(_wave_of(t), []).append(t.get("id"))
        orch["waves"] = [rebuilt[w] for w in sorted(rebuilt)]
        order["orchestration"] = orch
        notes.append("orchestration.waves did not partition all tasks; "
                     "rebuilt from task waves")

    # Every contract.owner_task must reference a real task.
    for c in order.get("contracts") or []:
        owner = c.get("owner_task")
        if owner and owner not in task_ids:
            notes.append(f"contract {c.get('name')} owner_task {owner!r} is not a task")
        if not str(c.get("source_path", "") or "").strip():
            notes.append(f"contract {c.get('name')} has no source_path -- the "
                         f"contract-freeze gate cannot enforce it")

    for n in notes:
        log.warning("stamp consistency: %s", n)
    return notes


def stamp(order):
    """Compute the deterministic buildability score and validate/repair the plan.
    Mutates `order` (sets order['buildability'] and may repair the DAG) and returns
    the buildability dict. The score has teeth: it measures real collision risk and
    parallelism from the actual file assignments, not a model's self-assessment."""
    _normalize_order(order)  # coerce malformed model output before any analysis
    notes = _validate_and_repair(order)
    tasks = order.get("tasks") or []
    contracts = order.get("contracts") or []
    n = len(tasks)

    # Group tasks by wave; count same-wave pairs that share a file (collisions).
    by_wave = {}
    for t in tasks:
        by_wave.setdefault(_wave_of(t), []).append(t)
    same_wave_pairs, collisions = 0, 0
    for wave_tasks in by_wave.values():
        for i in range(len(wave_tasks)):
            for j in range(i + 1, len(wave_tasks)):
                same_wave_pairs += 1
                fi = set(wave_tasks[i].get("files") or [])
                fj = set(wave_tasks[j].get("files") or [])
                if fi & fj:
                    collisions += 1
    collision_safety = 1 - collisions / max(1, same_wave_pairs)

    # One-writer-rate: of the distinct files written, how many are written by exactly
    # one task.
    writers = {}
    for t in tasks:
        for f in (t.get("files") or []):
            writers[f] = writers.get(f, 0) + 1
    distinct_files = len(writers)
    one_writer = sum(1 for c in writers.values() if c == 1)
    one_writer_rate = one_writer / max(1, distinct_files)

    isolation = round(100 * (0.5 * collision_safety + 0.5 * one_writer_rate))

    # Parallelism: 0 if fully sequential (W == n), ~100 if highly parallel (W == 1).
    W = len(by_wave)
    parallelism = round(100 * (n - W) / max(1, n - 1))

    # Contract coverage: every depended-upon task ideally exposes a typed seam.
    depended_upon = set()
    for t in tasks:
        for d in (t.get("depends_on") or []):
            depended_upon.add(d)
    contract_coverage = round(
        100 * min(1, len(contracts) / max(1, len(depended_upon))))

    testability = round(
        100 * sum(1 for t in tasks if str(t.get("test", "")).strip()) / max(1, n))

    # --- executability: is the order materialized artifact, or prose? (the DNA test) ---
    # Topology alone (a clean DAG) can score a prose skeleton an undeserved B. These
    # factors -- computed by seasar_verify, the SAME gate the orchestrator runs before
    # any agent starts -- grade whether the contracts, fixtures, and scaffold are REAL.
    exf = seasar_verify.executability_factors(order)
    contracts_compile = exf["contracts_compile"]
    fixtures_materialized = exf["fixtures_materialized"]
    scaffold_runnable = exf["scaffold_runnable"]
    vres = seasar_verify.verify_order(order)
    # self-check: a structurally-broken DAG scores 0; otherwise the merge/handoff
    # completeness (handoff protocol, contract evolution, per-agent DoD) -- the WARN
    # checks NOT already graded by contracts_compile/fixtures/scaffold, so the 8 factors
    # stay orthogonal (no double-counting).
    self_check_passes = 0 if not vres["ok"] else vres["independent_executability"]

    # Executability-weighted (0.55) so a prose-only order drops from B to F; the four
    # topology factors keep 0.45 -- a sound DAG is necessary but no longer sufficient.
    score = round(0.15 * isolation + 0.10 * parallelism
                  + 0.10 * contract_coverage + 0.10 * testability
                  + 0.20 * contracts_compile + 0.10 * fixtures_materialized
                  + 0.10 * scaffold_runnable + 0.15 * self_check_passes)

    src_n = sum(1 for c in contracts if str(c.get("source", "")).strip())
    rationale = (f"{n} tasks across {W} wave(s); {collisions} same-wave file "
                 f"collision(s) of {same_wave_pairs} pair(s); "
                 f"{one_writer}/{max(1, distinct_files)} files single-writer; "
                 f"{len(contracts)} contract(s) for {len(depended_upon)} "
                 f"depended-upon task(s); {src_n}/{len(contracts)} carry source; "
                 f"self-check {'ok' if vres['ok'] else 'BROKEN'} "
                 f"(executability {vres['executability']}/100)")
    if notes:
        rationale += " | repaired: " + "; ".join(notes)

    buildability = {
        "score": score,
        "grade": _grade(score),
        "rationale": rationale,
        "factors": {
            "isolation": isolation,
            "parallelism": parallelism,
            "contract_coverage": contract_coverage,
            "testability": testability,
            "contracts_compile": contracts_compile,
            "fixtures_materialized": fixtures_materialized,
            "scaffold_runnable": scaffold_runnable,
            "self_check_passes": self_check_passes,
        },
    }
    order["buildability"] = buildability
    # the merge-queue: a deterministic topo order the orchestrator merges in (re-running
    # the gate after each), so N PRs green-in-isolation can't poison the wave out of order.
    orch = order.get("orchestration")
    if isinstance(orch, dict):
        orch["merge_order"] = _merge_order(order)
    return buildability


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------

def _order_path(order_id):
    return ORDERS_DIR / f"{order_id}.json"


def load_order(order_id):
    """Load a stored order by id, or None if missing/unreadable."""
    return argo_store.load_json(_order_path(order_id), None)


def _persist(order):
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(_order_path(order["id"]), order)


# ---------------------------------------------------------------------------
# Internal cost accounting (OPERATOR-ONLY). Cost never enters the order object,
# the SSE stream, the /api/order payload, or the bundle -- it is recorded to an
# append-only ledger + the operator log so we can track unit economics (avg
# $/build order, the Opus-CAST share). Tokens are counted with Anthropic's
# authoritative count_tokens (free; exact for the Claude calls, ~95% of cost);
# the two non-Anthropic adversaries are counted with the Claude tokenizer as a
# close proxy and priced at public-rate estimates (flagged `estimated`).
# ---------------------------------------------------------------------------

COSTS_PATH = Path(os.environ.get(
    "SEASAR_COSTS_LOG", str(ROOT / "data" / "seasar_costs.jsonl")))

# $ per 1M tokens (input, output). Anthropic rates are authoritative (claude-api);
# gpt-5/grok are public-rate ESTIMATES, env-overridable. Unknown models price at 0.
_PRICE = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-5": (1.25, 10.00),
    "grok-4.3": (3.00, 15.00),
}

_tok_client = None


def _count_tokens(text, model):
    """Authoritative token count via Anthropic count_tokens (free, not billed).
    Non-Claude models use a Claude tokenizer as a close proxy."""
    global _tok_client
    if _tok_client is None:
        import anthropic
        _tok_client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"].strip())
    cm = model if str(model).startswith("claude") else "claude-sonnet-4-6"
    return _tok_client.messages.count_tokens(
        model=cm, messages=[{"role": "user", "content": text or "x"}]
    ).input_tokens


def _record_cost(order, stages, compile_ms):
    """Count tokens + price each stage, append ONE record to the cost ledger, and
    log a one-line operator summary. Best-effort: a metering failure must never sink
    a completed (paid) compile. Does NOT mutate `order` -- cost stays operator-only.
    `stages` is a list of {stage, model, input, output} (the raw prompt/response text
    of each paid call)."""
    try:
        def measure(s):
            ti = _count_tokens(s["input"], s["model"])
            to = _count_tokens(s["output"], s["model"])
            pin, pout = _PRICE.get(s["model"], (0.0, 0.0))
            return {
                "stage": s["stage"], "model": s["model"],
                "input_tokens": ti, "output_tokens": to,
                "cost_usd": round(ti / 1e6 * pin + to / 1e6 * pout, 6),
                "estimated": not str(s["model"]).startswith("claude"),
            }
        with ThreadPoolExecutor(max_workers=max(1, len(stages))) as pool:
            by_stage = list(pool.map(measure, stages))
        total = round(sum(s["cost_usd"] for s in by_stage), 6)
        record = {
            "id": order.get("id"), "title": order.get("title"),
            "created_at": _now_iso(), "total_cost_usd": total,
            "total_input_tokens": sum(s["input_tokens"] for s in by_stage),
            "total_output_tokens": sum(s["output_tokens"] for s in by_stage),
            "compile_ms": compile_ms, "by_stage": by_stage,
        }
        COSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with COSTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        cast = next((s for s in by_stage if s["stage"] == "cast"), None)
        share = (f", cast {cast['cost_usd'] / total * 100:.0f}%"
                 if cast and total else "")
        log.info("seasar cost: %s $%.4f (%d in / %d out tok%s)", order.get("id"),
                 total, record["total_input_tokens"],
                 record["total_output_tokens"], share)
    except Exception:
        log.warning("seasar: cost accounting failed for %s", order.get("id"),
                    exc_info=True)


def _print_costs_summary():
    """Operator CLI: aggregate unit economics from the ledger."""
    if not COSTS_PATH.exists():
        print("no cost ledger yet")
        return
    recs = [json.loads(l) for l in COSTS_PATH.read_text().splitlines() if l.strip()]
    if not recs:
        print("cost ledger empty")
        return
    n = len(recs)
    tot = sum(r["total_cost_usd"] for r in recs)
    avg_in = sum(r["total_input_tokens"] for r in recs) // n
    avg_out = sum(r["total_output_tokens"] for r in recs) // n
    print(f"{n} build orders  |  total ${tot:.2f}  |  avg ${tot / n:.3f}/order")
    print(f"avg tokens: {avg_in} in / {avg_out} out  |  ledger: {COSTS_PATH}")


# ---------------------------------------------------------------------------
# The pipeline -- a plain SYNC generator yielding complete SSE event strings.
# Starlette iterates a sync generator in a threadpool, so this stays sync.
# ---------------------------------------------------------------------------

def _sse(event):
    return f"data: {json.dumps(event)}\n\n"


def compile_stream(idea, stack="", scope="mvp", agents=4):
    """Compile a BUILD ORDER, yielding one complete SSE event per stage boundary.

    Yields, in order:
      smelt running/done, debate running/done, cast running/done,
      stamp running/done, then {"stage":"complete","order": <full BuildOrder>}.
    On any fatal error yields {"stage":"error","message": "<honest reason>"} and
    stops. Persists the finished order to data/seasar_orders/<id>.json before the
    complete event. Sets meta.compile_ms from wall-clock.

    A plain sync generator (no async) so Starlette's StreamingResponse threadpool
    iteration works and the guarded, blocking model calls run normally.
    """
    t0 = time.monotonic()
    idea = (idea or "").strip()
    if not idea:
        yield _sse({"stage": "error", "message": "No idea provided to compile."})
        return
    scope = scope if scope in ("weekend", "mvp", "product") else "mvp"
    try:
        # clamp both ends: floor at 1, ceil at 20 (a sane fleet) so an unbounded
        # user value can't bloat the paid prompt / produce a nonsensical fleet size.
        agents = min(20, max(1, int(agents)))
    except (TypeError, ValueError):
        agents = 4

    try:
        # ---- SMELT ----
        yield _sse({"stage": "smelt", "status": "running"})
        brief, smelt_model, smelt_raw = smelt(idea, stack, scope, agents)
        yield _sse({"stage": "smelt", "status": "done", "data": {
            "normalized_idea": brief["normalized_idea"],
            "inferred_stack": brief["inferred_stack"],
            "assumptions": brief["assumptions"],
        }})

        # ---- DEBATE ----
        yield _sse({"stage": "debate", "status": "running"})
        assigned = rehearse._assign_adversary_models(rehearse.ADVERSARIES)
        critiques = rehearse.run_adversaries(brief["normalized_idea"],
                                             assigned=assigned)
        if critiques is None:
            yield _sse({"stage": "error", "message": (
                "No model available to run the debate (no provider key set). "
                "Set ANTHROPIC_API_KEY / OPENAI_API_KEY / XAI_API_KEY.")})
            return
        debate = {
            "critic": critiques.get("critic", ""),
            "user": critiques.get("user", ""),
            "ops": critiques.get("ops", ""),
            "models": assigned or {},
        }
        yield _sse({"stage": "debate", "status": "done", "data": debate})

        # ---- CAST ----
        yield _sse({"stage": "cast", "status": "running"})
        partial, cast_model, cast_raw = cast(idea, brief, scope, agents, critiques)
        yield _sse({"stage": "cast", "status": "done"})

        # ---- assemble the order, then STAMP it (stamp normalizes it) ----
        order = dict(partial)
        order["id"] = f"order-{uuid.uuid4().hex[:6]}"
        order["idea"] = idea
        order.setdefault("scope", scope)
        order.setdefault("agent_count", agents)
        if not str(order.get("stack", "")).strip():
            order["stack"] = brief["inferred_stack"]
        order["debate"] = debate

        yield _sse({"stage": "stamp", "status": "running"})
        buildability = stamp(order)
        yield _sse({"stage": "stamp", "status": "done",
                    "data": {"buildability": buildability}})

        compile_ms = int((time.monotonic() - t0) * 1000)
        order["meta"] = {
            "created_at": _now_iso(),
            "compile_ms": compile_ms,
            "judge_model": cast_model,
        }

        try:
            _persist(order)
        except OSError:
            log.warning("seasar: could not persist order %s", order.get("id"),
                        exc_info=True)

        # Internal cost accounting from the raw prompts/responses of each paid call.
        # Reconstructed deterministically (same builders the calls used); stays out
        # of `order`, so it never reaches the client. Best-effort inside _record_cost.
        stages = [{
            "stage": "smelt", "model": smelt_model,
            "input": _SMELT_SYSTEM + _smelt_prompt(idea, stack, scope, agents),
            "output": smelt_raw,
        }]
        for role in ("critic", "user", "ops"):
            stages.append({
                "stage": f"debate:{role}",
                "model": (assigned or {}).get(role, ""),
                "input": rehearse.ADVERSARY_SYSTEM + rehearse._adversary_prompt(
                    rehearse.ADVERSARIES[role], brief["normalized_idea"]),
                "output": critiques.get(role, ""),
            })
        stages.append({
            "stage": "cast", "model": cast_model,
            "input": _CAST_SYSTEM + _cast_prompt(idea, brief, scope, agents, critiques),
            "output": cast_raw,
        })
        _record_cost(order, stages, compile_ms)

        yield _sse({"stage": "complete", "order": order})

    except (ValueError, RuntimeError) as exc:
        # Expected boundaries: model parse failure (ValueError) or no-model
        # (RuntimeError). Honest, specific reason.
        log.warning("seasar compile failed: %s", exc, exc_info=True)
        yield _sse({"stage": "error", "message": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        # Outermost net: a paid pipeline must surface, never swallow, the real cause.
        log.error("seasar compile crashed", exc_info=True)
        yield _sse({"stage": "error",
                    "message": f"Unexpected error: {type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# BUNDLE -- turn a finished order into a downloadable .zip of real files.
# ---------------------------------------------------------------------------

def _slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "build-order").lower()).strip("-")
    return s or "build-order"


def _md_readme(order, root):
    b = order.get("buildability", {})
    f = b.get("factors", {})
    return f"""# {order.get('title', 'Build Order')}

{order.get('tagline', '')}

> {order.get('tagline', '')}

## Buildability: {b.get('score', 0)} ({b.get('grade', '?')})

{b.get('rationale', '')}

| factor | score |
| --- | --- |
| isolation | {f.get('isolation', 0)} |
| parallelism | {f.get('parallelism', 0)} |
| contract coverage | {f.get('contract_coverage', 0)} |
| testability | {f.get('testability', 0)} |
| contract source | {f.get('contracts_compile', 0)} |
| fixtures | {f.get('fixtures_materialized', 0)} |
| scaffold | {f.get('scaffold_runnable', 0)} |
| self-check | {f.get('self_check_passes', 0)} |

- **Idea:** {order.get('idea', '')}
- **Stack:** {order.get('stack', '')}
- **Scope:** {order.get('scope', '')}
- **Fleet size:** {order.get('agent_count', 0)} agents

## Run the fleet

1. Each agent works in its own git worktree (see `work-orders/`) so writes never
   collide. Point each autonomous coding agent at its work order file.
2. Build in waves (see `tasks.md` / `orchestration.md`): all tasks in a wave run
   in parallel; a wave starts only after the previous wave's quality gates pass.
3. Agree the typed seams first (see `contracts/`) -- the consistency check in
   `orchestration.md` must pass before any agent starts coding.

This Build Order was compiled by Seasar. Order id: `{order.get('id', '')}`.
"""


def _md_constitution(order):
    items = order.get("constitution") or []
    body = "\n".join(f"- {c}" for c in items) or "- (none specified)"
    return f"# Constitution\n\nNon-negotiable invariants every agent must hold:\n\n{body}\n"


def _md_spec(order):
    spec = order.get("spec") or {}
    ac = "\n".join(f"- {c}" for c in spec.get("acceptance_criteria", [])) or "- (none)"
    ng = "\n".join(f"- {c}" for c in spec.get("non_goals", [])) or "- (none)"
    ex = "\n".join(
        f"- input: `{e.get('input', '')}` -> output: `{e.get('output', '')}`"
        for e in spec.get("examples", [])
    ) or "- (none)"
    return f"""# Spec

## What
{spec.get('what', '')}

## Why
{spec.get('why', '')}

## Acceptance criteria
{ac}

## Non-goals
{ng}

## Examples
{ex}
"""


def _md_hardening(order):
    """hardening.md -- the red-team objections and how the plan answers each. The
    'show your work' of the adversarial debate."""
    items = order.get("hardening") or []
    if not items:
        return "# How the red team hardened it\n\n(no hardening recorded)\n"
    out = ["# How the red team hardened it\n",
           "Each surviving objection from the debate, and how this plan answers it.\n"]
    for h in items:
        src = h.get("source", "")
        head = h.get("concern", "")
        out.append(f"## {head}" + (f"  _(raised by {src})_" if src else ""))
        out.append(f"{h.get('resolution', '')}\n")
    return "\n".join(out) + "\n"


def _md_provisions(order):
    """provisions.md -- the human-in-the-loop pre-flight: everything to collect
    before the fleet starts, so the build never stalls mid-flight asking for it."""
    items = order.get("provisions") or []
    if not items:
        return "# Before you build\n\nNo human-in-the-loop inputs required.\n"

    def fmt(p):
        bits = [f"### {p.get('name', '')}  ({p.get('kind', '')})"]
        if p.get("env_var"):
            bits.append(f"- env var: `{p['env_var']}`")
        if p.get("needed_by"):
            bits.append(f"- needed by: {', '.join(p['needed_by'])}")
        if p.get("how_to_get"):
            bits.append(f"- how to get: {p['how_to_get']}")
        if p.get("recommended"):
            bits.append(f"- recommended default: {p['recommended']}")
        return "\n".join(bits)

    blocking = [p for p in items if p.get("blocking")]
    optional = [p for p in items if not p.get("blocking")]
    out = ["# Before you build\n",
           "Collect these once, up front, so the agent fleet never stalls mid-build "
           "asking for them.\n",
           "## Required (the build is gated on these)\n",
           "\n\n".join(fmt(p) for p in blocking) or "_none_",
           "\n## Optional (sane defaults applied; override if you want)\n",
           "\n\n".join(fmt(p) for p in optional) or "_none_",
           "\nPut secrets/config into `.env` (see `.env.example`) -- agents read from "
           "the environment, never from you."]
    return "\n".join(out) + "\n"


def _env_example(order):
    """.env.example -- generated from provisions that declare an env_var. The human
    fills it once; the whole fleet reads from it instead of stopping to ask."""
    lines = ["# Fill these in and save as .env -- the agent fleet reads from here.",
             "# Generated by Seasar from the build order's provisions.\n"]
    have = False
    for p in (order.get("provisions") or []):
        ev = p.get("env_var")
        if not ev:
            continue
        have = True
        comment = p.get("name", "")
        if p.get("how_to_get"):
            comment += f" -- {p['how_to_get']}"
        if comment.strip():
            lines.append(f"# {comment}".rstrip())
        lines.append(f"{ev}={p.get('recommended', '')}")
        lines.append("")
    if not have:
        lines.append("# (no environment variables required)")
    return "\n".join(lines) + "\n"


def _md_agents(order):
    """AGENTS.md -- the build context every agent reads first: stack, project
    structure, and the boundary tier split into MACHINE-CHECKED gates (CI blocks on
    these) vs prose INVARIANTS (the constitution, not all gated yet) so an agent can
    see what is enforced vs what it must honor by hand, plus the propose-to-owner
    protocol for changing a contract it does not own."""
    scaffold = order.get("repo_scaffold") or []
    structure = "\n".join(f"- `{e.get('path', '')}` -- {e.get('purpose', '')}"
                          for e in scaffold) or "- (see repo_scaffold)"
    gates = [g for g in (order.get("quality_gates") or []) if g.get("blocks_merge")]
    gate_lines = "\n".join(
        f"- `{g.get('name', '')}` MUST pass: {g.get('threshold', '')}" for g in gates
    ) or "- (no blocking gates declared -- itself a buildability gap)"
    constitution = order.get("constitution") or []
    invariants = "\n".join(f"- {c}" for c in constitution) or "- follow the spec"
    orch = order.get("orchestration") or {}
    evolution = (orch.get("contract_evolution") or orch.get("handoff_protocol")
                 or "stop and ask the orchestrator before changing a contract you do not own")
    return f"""# AGENTS.md

Build context for autonomous coding agents on this Build Order.

## Stack
{order.get('stack', '')}

## Project structure
{structure}

## Boundaries
### Always (machine-checked -- CI blocks merge on failure)
{gate_lines}
- Always write the test named on your task before marking it done.
- Always stay inside the files listed on your task -- one writer per file.

### Invariants (the constitution -- never violate; not every one is gated yet)
{invariants}

### Ask (propose, never guess)
- If a task needs to write a file owned by another task, STOP -- do not edit it.
- To change a contract you do not own, follow contract_evolution: {evolution}
- File a CCR in CONTRACT_CHANGES.md (the owner amends + bumps version; CI re-verifies). A contract source change with no matching CCR fails `check-contract-freeze.py`.
- Record the request in HANDOFF.md; never silently guess a shape.

### Never
- Never edit a file outside your assigned task's `files` list.
- Never merge past a failing quality gate (see `orchestration.md`).
- Never change a published contract without re-running `verify-build-order`.
- Never resolve a DECISIONS.md item silently -- a surviving `SEASAR_DECIDE_` sentinel fails the build.

## Commands
- Run your task's test (see `tasks.md`) and confirm its acceptance gate is green.
- Run `python3 scripts/verify-build-order.py build-order.json` before you start and before you hand off.
- Resolve your DECISIONS.md items, then `python3 scripts/assert-no-sentinel.py` -- it fails while any sentinel survives.
- Blocking-gate tests are pre-authored in `tests/gates/` -- run them, do NOT rewrite them (you cannot grade your own work).
- `python3 scripts/check-ownership.py --agent "<your agent>" <changed files>` -- stay in your lane (one writer per file).
- CI (`.github/workflows/seasar-gate.yml`) runs build-order + forced-stop + ownership + project verify as a REQUIRED check; a red gate blocks merge.
- Build proceeds in waves; wait for the previous wave's gates before starting.
"""


def _md_tasks(order):
    by_wave = {}
    for t in (order.get("tasks") or []):
        by_wave.setdefault(_wave_of(t), []).append(t)
    out = ["# Tasks (the DAG)\n"]
    for w in sorted(by_wave):
        wave_tasks = by_wave[w]
        out.append(f"## Wave {w} (parallel)\n")
        for t in wave_tasks:
            par = " [P]" if len(wave_tasks) > 1 else ""
            files = ", ".join(f"`{f}`" for f in (t.get("files") or [])) or "(none)"
            deps = ", ".join(t.get("depends_on") or []) or "none"
            out.append(
                f"### {t.get('id')}{par} -- {t.get('title', '')}\n"
                f"- role: {t.get('agent_role', '')}\n"
                f"- files: {files}\n"
                f"- depends_on: {deps}\n"
                f"- test: {t.get('test', '')}\n"
                f"- acceptance: {t.get('acceptance', '')}\n"
            )
    return "\n".join(out) + "\n"


def _md_orchestration(order):
    orch = order.get("orchestration") or {}
    waves = orch.get("waves") or []
    wave_lines = "\n".join(
        f"- Wave {i + 1}: {', '.join(w)}" for i, w in enumerate(waves)
    ) or "- (none)"
    gates = "\n".join(
        f"- **{g.get('name', '')}** -- threshold: {g.get('threshold', '')} "
        f"(blocks merge: {bool(g.get('blocks_merge'))})"
        for g in (order.get("quality_gates") or [])
    ) or "- (none)"
    return f"""# Orchestration

- **Topology:** {orch.get('topology', 'orchestrator-worker')}

## Wave schedule
{wave_lines}

## Quality gates
{gates}

## Consistency check
{orch.get('consistency_check', '')}
"""


def _md_contract(c):
    out = [f"# Contract: {c.get('name', '')}\n",
           f"- **kind:** {c.get('kind', '')}",
           f"- **owner task:** {c.get('owner_task', '')}"]
    if c.get("source_path"):
        out.append(f"- **source file:** `{c.get('source_path')}`")
    if c.get("source_lang"):
        out.append(f"- **language:** {c.get('source_lang')}")
    out.append(f"\n## Rationale\n{c.get('detail', '')}")
    src = c.get("source") or ""
    if src.strip():
        out.append(f"\n## Source (canonical -- IMPORT this, do not re-derive)\n"
                   f"```{c.get('source_lang') or ''}\n{src}\n```")
    return "\n".join(out) + "\n"


def _md_work_order(wo, order=None):
    """The self-contained agent packet: brief + allowed/forbidden files + required
    commands + per-task acceptance + definition-of-done + handoff -- everything one
    agent needs without bouncing between docs. It now carries the allowed-files,
    forbidden-contracts, and definition_of_done the buildability score rewards, so the
    executability the score grades actually reaches the agent who reads the packet."""
    order = order or {}
    tasks_by_id = {t.get("id"): t for t in (order.get("tasks") or []) if t.get("id")}
    my_ids = wo.get("task_ids") or []
    my_tasks = [tasks_by_id[i] for i in my_ids if i in tasks_by_id]
    allowed = sorted({f for t in my_tasks for f in (t.get("files") or [])})
    # Contract files owned by OTHER tasks are forbidden -- propose a change, never edit.
    forbidden = sorted({
        c.get("source_path") for c in (order.get("contracts") or [])
        if c.get("source_path") and c.get("owner_task") not in my_ids
    })
    # Decisions this agent faces (anchored to its tasks or its files) -- it must RESOLVE
    # each, never guess; the SEASAR_DECIDE_ sentinel gate enforces it.
    allowed_set = set(allowed)
    my_decisions = [d for d in (order.get("decisions") or [])
                    if d.get("anchor_task") in my_ids
                    or (d.get("anchor_file") and d.get("anchor_file") in allowed_set)]
    out = [f"# Work order: {wo.get('agent', '')}\n",
           f"- **role:** {wo.get('role', '')}",
           f"- **task ids:** {', '.join(my_ids) or '(none)'}",
           f"- **worktree:** `{wo.get('worktree', '')}`",
           f"\n## Brief\n{wo.get('brief', '')}",
           "\n## Allowed files (you write ONLY these -- one writer per file)"]
    out += [f"- `{p}`" for p in allowed] or ["- (none listed)"]
    out.append("\n## Forbidden")
    out.append("- Any file not in Allowed above.")
    out += [f"- `{p}` (a contract owned by another task -- propose a change, never edit)"
            for p in forbidden]
    if my_decisions:
        out.append("\n## Decisions you must RESOLVE (no silent guesses)")
        for d in my_decisions:
            opts = " | ".join(d.get("options") or []) or "(open)"
            rec = d.get("recommended")
            # Reference by id, NOT the literal sentinel token -- a matchable token in the
            # packet would keep assert-no-sentinel red even after the decision is resolved.
            out.append(f"- {d.get('id')}: {d.get('question', '')}  [{opts}]"
                       + (f" (recommended: {rec})" if rec else "")
                       + f" -- resolve decision {d.get('id')} in DECISIONS.md (do not guess)")
    out.append("\n## Required before done")
    out.append("- The test named on each assigned task passes.")
    out.append("- The repo verify pipeline is green: typecheck + tests + `verify-build-order`.")
    out.append("- Every blocking quality gate touching your files is green.")
    out.append("- `python3 scripts/assert-no-sentinel.py` is green (every decision resolved).")
    out.append(f"- `python3 scripts/check-ownership.py --agent \"{wo.get('agent', '')}\" "
               f"<changed files>` is green (you stayed in your lane).")
    out.append("\n## Acceptance (per task)")
    out += [f"- {t.get('id')}: {t.get('acceptance', '')}" for t in my_tasks] or ["- (none)"]
    out.append("\n## Definition of done")
    out.append(wo.get("definition_of_done", "") or "(not specified)")
    out.append("\n## Handoff")
    out.append("- Update your task status in tasks.md.")
    out.append("- Record any contract ambiguity or change request in HANDOFF.md "
               "(never silently guess).")
    return "\n".join(out) + "\n"


def _sentinel(d):
    """The unique forced-stop token for a decision: SEASAR_DECIDE_<id>."""
    return "SEASAR_DECIDE_" + (d.get("id") or "X")


def _md_decisions(order):
    """DECISIONS.md -- the forced-stop ledger. Each decision is an ambiguity the spec
    does NOT settle, seeded with a unique SEASAR_DECIDE_<id> sentinel that the
    assert-no-sentinel gate refuses to let survive -- so an agent cannot ship a silent
    guess. Resolving a decision = replacing its sentinel line with the chosen option +
    where it is enforced."""
    decisions = order.get("decisions") or []
    out = ["# DECISIONS -- resolve every one before the build goes green\n",
           "Each item is an ambiguity the spec does NOT settle -- exactly where an agent "
           "would otherwise guess silently. Pick an option, implement it, and REPLACE its "
           "`SEASAR_DECIDE_<id>` checkbox line below with a `RESOLVED_<id>: <choice> | "
           "enforced at <file>:<line>` line. `scripts/assert-no-sentinel.py` fails the "
           "build while ANY sentinel survives OR any decision lacks its RESOLVED_<id> "
           "line -- so a decision cannot be closed by silently deleting its row.\n"]
    if not decisions:
        out.append("_No open decisions: the spec settled every ambiguity._\n")
        return "\n".join(out)
    for d in decisions:
        opts = " | ".join(d.get("options") or []) or "(open)"
        rec = d.get("recommended") or ""
        anchor = ", ".join(x for x in (d.get("anchor_task"), d.get("anchor_file")) if x)
        out.append(f"## {d.get('id', '?')} -- {d.get('question', '')}")
        if anchor:
            out.append(f"- anchor: {anchor}")
        out.append(f"- options: {opts}" + (f"   (recommended: {rec})" if rec else ""))
        if d.get("rationale"):
            out.append(f"- why: {d.get('rationale')}")
        out.append(f"- [ ] {_sentinel(d)} -- to resolve: replace this line with "
                   f"`RESOLVED_<id>: <chosen option> | enforced at <file>:<line>` "
                   f"(this decision's id is {d.get('id')})\n")
    return "\n".join(out)


# The forced-stop gate, emitted into every bundle. Greps the produced repo for any
# unresolved decision sentinel and fails the build while one survives -- so a
# confidently-wrong SILENT guess cannot ship (the dominant autonomous-fleet failure).
_ASSERT_NO_SENTINEL = r'''#!/usr/bin/env python3
"""assert-no-sentinel -- the forced-stop gate. Fails (exit 1) while ANY decision sentinel
(SEASAR_DECIDE_<id>) survives under the given root (default: cwd), OR any decision from
build-order.json lacks its RESOLVED_<id> line in DECISIONS.md -- so a decision cannot be
closed by a silent guess NOR by quietly deleting its ledger row. Run it in CI and before
every handoff: `python3 scripts/assert-no-sentinel.py`.
"""
import json
import os
import re
import sys

PATTERN = re.compile(r"SEASAR_DECIDE_[A-Za-z0-9]+")
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".next"}


def _decision_ids(root):
    try:
        with open(os.path.join(root, "build-order.json"), encoding="utf-8") as fh:
            order = json.load(fh)
    except (OSError, ValueError):
        return []
    return [str(d.get("id")) for d in (order.get("decisions") or [])
            if isinstance(d, dict) and d.get("id")]


def main(root="."):
    self_path = os.path.realpath(__file__)
    hits, decisions_md = [], ""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if os.path.realpath(path) == self_path:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            if fn == "DECISIONS.md":
                decisions_md = text
            for i, line in enumerate(text.splitlines(), 1):
                if PATTERN.search(line):
                    hits.append(f"{path}:{i}: {line.strip()}")
    unresolved = [rid for rid in _decision_ids(root)
                  if ("RESOLVED_" + rid) not in decisions_md]
    if hits or unresolved:
        if hits:
            print("FORCED STOP: %d unresolved decision sentinel(s):" % len(hits))
            for h in hits:
                print("  " + h)
        if unresolved:
            print("FORCED STOP: %d decision(s) with no RESOLVED_<id> line in "
                  "DECISIONS.md: %s" % (len(unresolved), ", ".join(unresolved)))
        print("Resolve each: pick an option, implement it, write RESOLVED_<id> in "
              "DECISIONS.md, and remove the sentinel.")
        return 1
    print("OK: every decision resolved; no sentinels survive.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
'''


# Build-time one-writer-per-file enforcement, emitted into every bundle. A non-agent
# actor (CI/git) runs it, so an agent cannot write outside its lane by goodwill alone.
_CHECK_OWNERSHIP = r'''#!/usr/bin/env python3
"""check-ownership -- build-time one-writer-per-file enforcement, run by CI (not an agent).

Reads build-order.json from $SEASAR_ROOT or cwd. Three modes:
  audit  (no flag)       : fail if any file is written by more than one task.
  lanes  (--lanes)       : given a changed-file set (args or newline stdin), fail unless
                           every lane-controlled changed file fits inside ONE agent's lane
                           (its tasks' files) -- a PR may not cross two agents' lanes.
                           Files in no lane (shared/undeclared) are ignored.
  agent  (--agent NAME)  : like lanes, for a single named agent self-checking its diff.

Paths are normpath-compared ('./src/a.ts' == 'src/a.ts'). Exact-path convention: an agent
must DECLARE on its task any file it creates. Empty changed-file input is an error, not a
pass -- a broken/empty diff is not a clean lane.
"""
import json
import os
import sys


def _norm(p):
    return os.path.normpath(p.strip())


def _order(root):
    with open(os.path.join(root, "build-order.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _file_owners(order):
    owners = {}
    for t in (order.get("tasks") or []):
        if isinstance(t, dict):
            for f in (t.get("files") or []):
                owners.setdefault(_norm(f), set()).add(t.get("id"))
    return owners


def _agent_lanes(order):
    tasks = {t.get("id"): t for t in (order.get("tasks") or []) if isinstance(t, dict)}
    lanes = {}
    for wo in (order.get("work_orders") or []):
        if isinstance(wo, dict) and wo.get("agent"):
            lane = lanes.setdefault(wo.get("agent"), set())
            for tid in (wo.get("task_ids") or []):
                lane.update(_norm(f) for f in (tasks.get(tid, {}).get("files") or []))
    return lanes


def _changed(argv_files):
    if argv_files:
        return argv_files
    if sys.stdin is None or sys.stdin.isatty():
        return None
    return [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]


def main(argv):
    root = os.environ.get("SEASAR_ROOT", ".")
    mode, agent, files, it = "audit", None, [], iter(argv)
    for a in it:
        if a == "--agent":
            agent, mode = next(it, ""), "agent"
        elif a == "--lanes":
            mode = "lanes"
        elif a == "--root":
            root = next(it, ".")
        else:
            files.append(a)
    try:
        order = _order(root)
    except (OSError, ValueError) as e:
        print("check-ownership: cannot load build-order.json (%s)" % e, file=sys.stderr)
        return 1

    if mode == "audit":
        shared = {f: ids for f, ids in _file_owners(order).items() if len(ids) > 1}
        if shared:
            print("OWNERSHIP VIOLATION: file(s) written by more than one task:")
            for f, ids in sorted(shared.items()):
                print("  %s <- %s" % (f, ", ".join(map(str, sorted(ids)))))
            return 1
        print("OK: every planned file has a single writer.")
        return 0

    if mode == "agent" and not agent:
        print("check-ownership: --agent given without a value", file=sys.stderr)
        return 2

    changed = _changed(files)
    if changed is None:
        print("check-ownership: %s mode needs changed files as args or piped on stdin"
              % mode, file=sys.stderr)
        return 2
    if not changed:
        print("check-ownership: no changed files supplied -- refusing to pass vacuously "
              "(a broken/empty diff is not a clean lane)", file=sys.stderr)
        return 1

    lanes = _agent_lanes(order)
    changed_norm = [_norm(f) for f in changed]

    if mode == "agent":
        if agent not in lanes:
            print("check-ownership: no work order for agent %r" % agent, file=sys.stderr)
            return 1
        outside = [f for f in changed_norm if f not in lanes[agent]]
        if outside:
            print("OUT OF LANE: agent %r changed file(s) outside its allowed set:" % agent)
            for f in outside:
                print("  " + f)
            print("Allowed: " + (", ".join(sorted(lanes[agent])) or "(none)"))
            return 1
        print("OK: agent %r stayed within its lane (%d file(s))." % (agent, len(changed)))
        return 0

    # lanes mode: the lane-controlled changed files must fit inside ONE agent's lane.
    in_lanes = {f for f in changed_norm if any(f in lane for lane in lanes.values())}
    if not in_lanes:
        print("OK: no lane-controlled files changed.")
        return 0
    if any(in_lanes <= lane for lane in lanes.values()):
        print("OK: all changed lane-files belong to one agent's lane.")
        return 0
    print("CROSS-LANE VIOLATION: changed files span more than one agent's lane:")
    for f in sorted(in_lanes):
        owners = sorted(a for a, lane in lanes.items() if f in lane)
        print("  %s <- %s" % (f, ", ".join(owners)))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


# The non-agent enforcement actor: a CI required status check the downstream fleet cannot
# route around. Wire it as a required check in branch protection so a red gate BLOCKS merge.
_CI_WORKFLOW = '''name: seasar-gate
# Wire this as a REQUIRED status check in branch protection: a red gate blocks the merge
# button, so the fleet is held by GitHub/git, not by an agent choosing to obey.
on:
  pull_request:
  push:
    branches: [main]
jobs:
  seasar-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Build-order integrity
        run: python3 scripts/verify-build-order.py build-order.json
      - name: Forced stop (no unresolved decisions)
        run: python3 scripts/assert-no-sentinel.py
      - name: One writer per file (plan audit)
        run: python3 scripts/check-ownership.py
      - name: Stay in lane (no cross-lane edits in this change)
        run: |
          set -o pipefail
          if [ -n "${{ github.base_ref }}" ]; then
            git fetch --no-tags origin "${{ github.base_ref }}" >/dev/null 2>&1 || true
            base="origin/${{ github.base_ref }}"
          else
            base="$(git rev-parse HEAD~1 2>/dev/null || echo HEAD)"
          fi
          changed="$(git diff --name-only "$base"...HEAD)"
          if [ -n "$changed" ]; then
            printf '%s\n' "$changed" | python3 scripts/check-ownership.py --lanes
          else
            echo "no changes to lane-check"
          fi
      - name: Contract freeze (no silent contract change without a CCR)
        run: |
          set -o pipefail
          if [ -n "${{ github.base_ref }}" ]; then
            git fetch --no-tags origin "${{ github.base_ref }}" >/dev/null 2>&1 || true
            base="origin/${{ github.base_ref }}"
          else
            base="$(git rev-parse HEAD~1 2>/dev/null || echo HEAD)"
          fi
          git diff --name-only "$base"...HEAD | python3 scripts/check-contract-freeze.py
      - name: Contract source compiles (substance prober)
        run: python3 scripts/check-contracts-compile.py
      - name: Project verify (typecheck + tests + gate predicates)
        run: |
          if [ -f package.json ]; then
            npm ci && npm run verify --if-present && npm test --if-present
          elif [ -f requirements.txt ]; then
            pip install -r requirements.txt && python -m pytest -q
          else
            echo "No recognized manifest -- set your project verify command here."
          fi
'''


def _md_merge_order(order):
    """MERGE_ORDER.md -- the order the orchestrator merges work in, re-running the gate
    after each merge so two PRs green-in-isolation can't poison a wave."""
    mo = (order.get("orchestration") or {}).get("merge_order") or []
    if not mo:
        return "# Merge order\n\n_(single task or unsequenced)_\n"
    lines = ["# Merge order\n",
             "Merge in THIS order, re-running `.github/workflows/seasar-gate.yml` after "
             "each merge (a PR green against the old base may not be green against the "
             "newly-merged HEAD):\n"]
    for n, tid in enumerate(mo, 1):
        lines.append(f"{n}. {tid}")
    return "\n".join(lines) + "\n"


def _md_contract_changes(order):
    """CONTRACT_CHANGES.md -- the async contract-change-request (CCR) ledger. Contracts are
    frozen and single-owner; a downstream agent that needs a change does NOT edit the file
    (lane enforcement forbids it) -- it files a CCR here. The owner amends the contract,
    bumps its version, and CI re-verifies every consumer."""
    contracts = [c for c in (order.get("contracts") or []) if isinstance(c, dict)]
    out = ["# CONTRACT CHANGES -- the async change-request ledger\n",
           "Contracts are FROZEN and single-owner. To change one you do not own, do NOT "
           "edit it -- file a CCR below; the owner amends it, bumps `version`, and CI "
           "re-verifies consumers. Any change to a contract's source file requires a "
           "matching `CCR <name>` line here, or `scripts/check-contract-freeze.py` fails "
           "the build.\n",
           "## Open requests",
           "<!-- one per line: `CCR <contract-name>: <task> needs <field/behavior> -- <why>` -->\n",
           "## Frozen contracts (owner / version)"]
    for c in contracts:
        out.append("- `%s` -- owner %s, v%s, file `%s`"
                   % (c.get("name", ""), c.get("owner_task", "?"),
                      c.get("version", "1.0.0"), c.get("source_path", "")))
    if not contracts:
        out.append("- (none)")
    return "\n".join(out) + "\n"


# A frozen contract may not change silently: any diff to a contract source file must carry
# a matching CCR in CONTRACT_CHANGES.md, or this emitted gate fails the build at CI time.
_CHECK_CONTRACT_FREEZE = r'''#!/usr/bin/env python3
"""check-contract-freeze -- a changed contract source file must have a logged CCR.

Given changed files (args or newline stdin) + build-order.json, fail (exit 1) for any
contract whose source_path is in the change but has no `CCR <name>` line in
CONTRACT_CHANGES.md -- so a frozen contract cannot change silently and consumers see the
ripple. File a CCR via the ritual in CONTRACT_CHANGES.md.
"""
import json
import os
import re
import sys


def main(argv):
    root = os.environ.get("SEASAR_ROOT", ".")
    files = [a for a in argv if not a.startswith("--")]
    if not files and not (sys.stdin is None or sys.stdin.isatty()):
        files = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    try:
        with open(os.path.join(root, "build-order.json"), encoding="utf-8") as fh:
            order = json.load(fh)
    except (OSError, ValueError) as e:
        print("check-contract-freeze: cannot load build-order.json (%s)" % e, file=sys.stderr)
        return 1
    try:
        with open(os.path.join(root, "CONTRACT_CHANGES.md"), encoding="utf-8") as fh:
            ledger = fh.read()
    except OSError:
        ledger = ""
    changed = {os.path.normpath(f) for f in files}
    unlogged = []
    for c in (order.get("contracts") or []):
        if not isinstance(c, dict):
            continue
        sp = c.get("source_path")
        if not (sp and os.path.normpath(sp) in changed):
            continue
        # Line-anchored, delimited match on the canonical "CCR <name>:" form -- a CCR for
        # ApiV2 must NOT satisfy Api (prefix), and a prose mention in another CCR's body
        # must not either. A nameless contract can't be logged -> always flagged.
        name = str(c.get("name", "") or "").strip()
        pat = re.compile(r"(?m)^\s*CCR\s+" + re.escape(name) + r"\s*:") if name else None
        if pat is None or not pat.search(ledger):
            unlogged.append(c.get("name"))
    if unlogged:
        print("CONTRACT FREEZE: changed contract(s) with no logged CCR in "
              "CONTRACT_CHANGES.md: " + ", ".join(map(str, unlogged)))
        print("File a CCR (see CONTRACT_CHANGES.md) so consumers re-verify.")
        return 1
    print("OK: no unlogged contract changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


# The substance prober, emitted into every bundle: actually COMPILE each contract source
# file in its language (not just check it is non-empty), so code that does not compile
# fails the build. python/json run here; other langs are covered by the project verify.
_CHECK_CONTRACTS_COMPILE = r'''#!/usr/bin/env python3
"""check-contracts-compile -- the substance prober. For every contract with source +
source_path, COMPILE/parse the file in its language (not merely check it is non-empty):
  python            -> py_compile (syntax only; never executed)
  json/json-schema  -> json.loads
Other languages are skipped here (the project verify step -- tsc/etc. -- compiles them).
A contract that looks like code but does not compile fails the build.
"""
import json
import os
import py_compile
import sys


def main(root="."):
    try:
        with open(os.path.join(root, "build-order.json"), encoding="utf-8") as fh:
            order = json.load(fh)
    except (OSError, ValueError) as e:
        print("check-contracts-compile: cannot load build-order.json (%s)" % e, file=sys.stderr)
        return 1
    fails, checked = [], 0
    for c in (order.get("contracts") or []):
        if not isinstance(c, dict):
            continue
        sp = c.get("source_path")
        if not (sp and str(c.get("source", "") or "").strip()):
            continue
        lang = str(c.get("source_lang", "") or "").lower()
        if lang not in ("python", "py", "json", "json-schema", "jsonschema"):
            continue
        path = os.path.join(root, sp)
        if not os.path.exists(path):
            fails.append("%s: source_path %s does not exist" % (c.get("name"), sp))
            continue
        try:
            if lang in ("python", "py"):
                py_compile.compile(path, doraise=True)
            else:
                with open(path, encoding="utf-8") as fh:
                    json.load(fh)
            checked += 1
        except (py_compile.PyCompileError, ValueError) as e:
            fails.append("%s (%s): %s" % (c.get("name"), lang, str(e).splitlines()[0]))
    if fails:
        print("CONTRACT COMPILE FAILED:")
        for f in fails:
            print("  " + f)
        return 1
    print("OK: %d contract source(s) compiled/parsed in-process "
          "(others covered by project verify)." % checked)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
'''


def _safe_bundle_path(raw):
    """Normalize a model-generated path for the bundle zip; return None if it would
    escape the root (Zip-Slip). Cross-platform: backslashes are normalized first so a
    Windows extractor can't be walked out, and ANY `..` SEGMENT (not just a leading one),
    an absolute path, or a collapse-to-root ('.') is rejected. Shared by every writer that
    emits a model-named file -- scaffold stubs, contract source, fixtures, boot files."""
    raw = (raw or "").replace("\\", "/").lstrip("/")
    if not raw:
        return None
    path = os.path.normpath(raw)
    if path in (".", "") or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
        return None
    return path


def _md_handoff():
    """HANDOFF.md -- the structured channel an agent writes to instead of silently
    guessing: an open question, a contract-change request, a blocked dependency. The
    orchestrator reads it between waves; an empty file is the healthy state."""
    return ("# HANDOFF\n\n"
            "Append an entry instead of silently guessing. The orchestrator reads this "
            "between waves.\n\n"
            "## Open questions / ambiguities\n"
            "- (task id) -- the decision you could not make from the spec/contracts\n\n"
            "## Contract-change requests (propose-to-owner)\n"
            "- (task id) -- contract `<name>` needs `<field/behavior>`; do NOT edit it "
            "yourself. The owner task amends it and re-runs verify-build-order.\n\n"
            "## Blocked\n"
            "- (task id) -- waiting on (task id / gate)\n")


def build_bundle(order):
    """Return a .zip (bytes) of the Build Order as real, runnable files: README,
    constitution, spec, AGENTS, tasks, orchestration, one doc per contract/work-order,
    the literal contract SOURCE files agents import, the wave-0 fixtures, the runnable
    scaffold_files boot skeleton, a HANDOFF.md channel, and the repo_scaffold stub map.
    Every model-named path is Zip-Slip-guarded. Stdlib zipfile + BytesIO only."""
    root = f"{_slug(order.get('title'))}-build-order"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()

        def put(p, data):
            # Write-once: the structural files are written first and win, so a model
            # scaffold_file / fixture / contract source that names the same path is
            # skipped rather than emitted as a duplicate zip entry (also dedupes two
            # model-named files that collide on one path).
            if p in seen:
                log.warning("seasar bundle: skipped duplicate path %r", p)
                return
            seen.add(p)
            z.writestr(p, data)

        put(f"{root}/README.md", _md_readme(order, root))
        put(f"{root}/constitution.md", _md_constitution(order))
        put(f"{root}/spec.md", _md_spec(order))
        put(f"{root}/hardening.md", _md_hardening(order))
        put(f"{root}/provisions.md", _md_provisions(order))
        put(f"{root}/.env.example", _env_example(order))
        put(f"{root}/AGENTS.md", _md_agents(order))
        put(f"{root}/tasks.md", _md_tasks(order))
        put(f"{root}/orchestration.md", _md_orchestration(order))
        # The raw order, for tooling.
        put(f"{root}/build-order.json", json.dumps(order, indent=2) + "\n")
        put(f"{root}/HANDOFF.md", _md_handoff())
        # Forced-stop: the decision ledger + the gate that refuses to let a sentinel ship.
        put(f"{root}/DECISIONS.md", _md_decisions(order))
        put(f"{root}/scripts/assert-no-sentinel.py", _ASSERT_NO_SENTINEL)
        # Build-time enforcement the fleet can't route around: ownership lint + CI gate.
        put(f"{root}/scripts/check-ownership.py", _CHECK_OWNERSHIP)
        put(f"{root}/scripts/check-contract-freeze.py", _CHECK_CONTRACT_FREEZE)
        put(f"{root}/scripts/check-contracts-compile.py", _CHECK_CONTRACTS_COMPILE)
        put(f"{root}/CONTRACT_CHANGES.md", _md_contract_changes(order))
        put(f"{root}/MERGE_ORDER.md", _md_merge_order(order))
        put(f"{root}/.github/workflows/seasar-gate.yml", _CI_WORKFLOW)
        for c in (order.get("contracts") or []):
            name = _slug(c.get("name") or "contract")
            put(f"{root}/contracts/{name}.md", _md_contract(c))
            # Contracts ARE source: emit the literal compilable file agents IMPORT.
            src = c.get("source") or ""
            sp = _safe_bundle_path(c.get("source_path"))
            if src.strip() and sp:
                put(f"{root}/{sp}", src if src.endswith("\n") else src + "\n")
        for wo in (order.get("work_orders") or []):
            name = _slug(wo.get("agent") or "agent")
            put(f"{root}/work-orders/{name}.md", _md_work_order(wo, order))
        # The literal runnable boot skeleton, at the repo root -- install + typecheck +
        # empty-test should be green here BEFORE any feature task starts.
        for s in (order.get("scaffold_files") or []):
            sp = _safe_bundle_path(s.get("path"))
            if not sp:
                log.warning("seasar bundle: skipped unsafe scaffold_file path %r", s.get("path"))
                continue
            body = s.get("body") or f"// {s.get('purpose', '')}\n"
            put(f"{root}/{sp}", body if body.endswith("\n") else body + "\n")
        # The wave-0 golden corpus every test runs against (a binary fixture ships its
        # reproducible generator alongside the target path instead of inline bytes).
        for f in (order.get("fixtures") or []):
            fp = _safe_bundle_path(f.get("path"))
            if not fp:
                log.warning("seasar bundle: skipped unsafe fixture path %r", f.get("path"))
                continue
            if f.get("binary") and not (f.get("body") or "").strip():
                gen = f.get("generator") or f"# {f.get('purpose', '')}\n"
                put(f"{root}/{fp}.generator.md",
                    f"# Generator for `{fp}`\n\n{f.get('purpose', '')}\n\n"
                    f"```\n{gen}\n```\n")
            else:
                body = f.get("body") or f"# {f.get('purpose', '')}\n"
                put(f"{root}/{fp}", body if body.endswith("\n") else body + "\n")
        # Compiler-authored gate predicates: the runnable test that JUDGES each gate, so
        # a feature agent inherits it instead of grading its own work (no tautology gate).
        for g in (order.get("quality_gates") or []):
            src = g.get("test_source") or ""
            if not src.strip():
                continue
            gp = _safe_bundle_path(g.get("test_path"))
            if not gp:
                log.warning("seasar bundle: gate %r has test_source but an empty/unsafe "
                            "test_path %r -- predicate NOT emitted", g.get("name"), g.get("test_path"))
                continue
            put(f"{root}/{gp}", src if src.endswith("\n") else src + "\n")
        # The feature-file map: paths owned by tasks; stub bodies the agents fill in.
        for e in (order.get("repo_scaffold") or []):
            path = _safe_bundle_path(e.get("path"))
            if not path:
                log.warning("seasar bundle: skipped unsafe scaffold path %r", e.get("path"))
                continue
            comment = f"# {e.get('purpose', '')}\n" if path.endswith(
                (".py", ".sh", ".rb", ".yml", ".yaml", ".toml")
            ) else f"// {e.get('purpose', '')}\n"
            put(f"{root}/scaffold/{path}", comment)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI smoke -- consume compile_stream, print each stage + the final score block.
# Makes real paid model calls (that is expected for the verify step).
# ---------------------------------------------------------------------------

def main():
    import sys

    if "--costs" in sys.argv:  # operator: print aggregate unit economics, exit
        _print_costs_summary()
        return

    idea = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or (
        "a CLI tool that watches a folder and auto-commits changes with "
        "AI-generated messages")
    print(f"\n=== Seasar compile ===\nIDEA: {idea}\n")
    final = None
    for chunk in compile_stream(idea):
        # Each chunk is a full SSE event string: "data: {...}\n\n".
        payload = chunk[len("data: "):].strip()
        event = json.loads(payload)
        stage = event.get("stage")
        if stage == "complete":
            final = event["order"]
            print("[complete] order assembled")
        elif stage == "error":
            print(f"[ERROR] {event['message']}")
            return
        else:
            line = f"[{stage}] {event.get('status', '')}"
            if stage == "smelt" and event.get("status") == "done":
                line += f"  stack={event['data']['inferred_stack']!r}"
            if stage == "debate" and event.get("status") == "done":
                line += f"  models={event['data']['models']}"
            if stage == "stamp" and event.get("status") == "done":
                line += f"  score={event['data']['buildability']['score']}"
            print(line)

    if final:
        b = final["buildability"]
        print("\n--- final order ---")
        print("keys:", sorted(final.keys()))
        print(f"id: {final['id']}")
        print(f"title: {final['title']}")
        print(f"tasks: {len(final.get('tasks', []))}  "
              f"waves: {len(final.get('orchestration', {}).get('waves', []))}  "
              f"contracts: {len(final.get('contracts', []))}  "
              f"work_orders: {len(final.get('work_orders', []))}")
        print("buildability:", json.dumps(b, indent=2))
        print(f"meta: {final.get('meta')}")
        names = zipfile.ZipFile(io.BytesIO(build_bundle(final))).namelist()
        print(f"\nbundle ({len(names)} files):")
        for n in names:
            print(" ", n)


if __name__ == "__main__":
    main()
