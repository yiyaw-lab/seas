"""seasar_verify -- the executable consistency + executability checker for a Build
Order.

One source of truth, shared by two callers:
  * seasar_compile.stamp() -- folds the result into the buildability score (the
    `self_check_passes` factor) so a structurally-broken or prose-only order can never
    grade well.
  * scripts/verify-build-order.py -- the CLI gate the orchestrator runs on a compiled
    order BEFORE any agent spends a token.

Two severities:
  ERROR (structural)     -- DAG integrity. A failure means agents WILL collide or block:
                            same-wave file collisions, forward/dangling deps, a wave set
                            that does not partition the tasks, a contract with no owner.
  WARN  (executability)  -- the new DNA requirements. A failure means the order is prose,
                            not runnable artifact: contracts with no compilable `source`,
                            fixtures with no body/generator, a scaffold that is not a
                            runnable skeleton, a missing merge/handoff protocol.

Pure stdlib; operates on the order dict; NEVER raises on a malformed or legacy order --
a missing field is a failed check, not a crash. Every accessor is defensive .get().
"""

import json
import re

ERROR = "error"   # structural -- agents WILL collide / block
WARN = "warn"     # executability -- the order is prose, not runnable artifact

# A scaffold is "runnable" if its literal files include a package manifest, a test/CI
# config, and an env template (each graded separately). str.endswith accepts a tuple.
_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod",
              "cargo.toml", "gemfile", "pom.xml", "build.gradle", "composer.json",
              "pubspec.yaml")
_TEST_HINTS = ("vitest", "jest", "playwright", "pytest", "cypress", ".github/workflows",
               "ci.yml", "ci.yaml", "test", "spec")
_ENV_HINTS = (".env.example", ".env.template", ".env.sample")
_FIXTURE_RE = re.compile(r"fixture", re.I)

# The RECOGNIZED behavioral aspects -- the runtime semantics a type signature omits.
# Mirrors seasar_compile._BEHAVIOR_ASPECTS (kept local, not imported: seasar_compile
# imports THIS module at load time, so importing back would be a circular import -- same
# zero-coupling reason _wave_of is duplicated). _normalize_behavior keeps ONLY these keys,
# so a contract whose behavior dict has only unrecognized keys carries no real spec; verify
# must require at least one of these, or a raw order's junk-key behavior passes a check the
# compiler would have emptied. If this list drifts, the cross-check in
# tests/test_seasar_bugbot_fixes.py fails.
_BEHAVIOR_ASPECTS = ("ordering", "idempotency", "errors", "pagination", "units")


# --- defensive accessors -----------------------------------------------------

def _dicts(order, key):
    return [x for x in (order.get(key) or []) if isinstance(x, dict)]


def _nonempty(v):
    return bool(str(v or "").strip())


def _strs(v):
    """Coerce a field that should be a list of strings. A list keeps its str members; a
    bare non-empty string becomes a ONE-element list (so files="a.py" is the path "a.py" --
    never char-iterated, and never dropped, so two same-wave tasks with the same scalar
    still collide); anything else -> []."""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return [v.strip()] if isinstance(v, str) and v.strip() else []


def _str_ids(v):
    """Coerce an ID-list field (depends_on / consumers / task_ids / waves) to a list of
    STRINGIFIED non-empty ids. Unlike _strs (which drops non-string members -- right for a
    files list), ids must stringify: a model may emit int task ids while the verify path
    runs on a RAW (un-normalized) order, and a mixed str/int comparison silently drops a
    real edge AND reports a phantom dangling one. Mirrors seasar_compile._normalize_order,
    which stringifies every id, so verify on a raw order agrees with verify on a compiled
    one. A bare scalar becomes a one-element list (never char-iterated)."""
    if isinstance(v, list):
        return [s for s in (str(x).strip() for x in v) if s]
    s = str(v).strip() if v is not None else ""
    return [s] if s else []


def _str_id(v):
    """Stringify a single id (owner_task / anchor_task) for comparison against the
    stringified task-id set; None/empty -> None so an absent id never fabricates a hit."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _source_parse_error(c):
    """Substance prober: return a syntax/parse error for a contract whose source we CAN
    check in-process (python via compile(), json via json.loads). compile() only
    SYNTAX-checks -- it never executes the source. Other languages return None (the emitted
    check-contracts-compile.py runs their real compiler at build time)."""
    src = c.get("source") or ""
    if not src.strip():
        return None
    lang = str(c.get("source_lang", "") or "").lower()
    try:
        if lang in ("python", "py"):
            compile(src, "<contract:%s>" % (c.get("name") or ""), "exec")
        elif lang in ("json", "json-schema", "jsonschema"):
            json.loads(src)
        else:
            return None
    except Exception as e:   # leaf helper: NEVER propagate -- verify_order must not raise
        return (str(e).splitlines() or ["parse error"])[0]
    return None


def _has_behavior(c):
    """True if a contract carries a behavioral spec -- the runtime semantics a type
    signature does NOT pin down (ordering, idempotency, error model, pagination, units) or
    a language-neutral interface IR. The exact place cross-agent semantic drift hides: two
    agents agree on the shape and silently disagree on the behavior."""
    beh = c.get("behavior")
    # Require at least one RECOGNIZED aspect with a non-empty value -- a behavior dict of
    # only unrecognized keys is junk the compiler's _normalize_behavior would drop, so it is
    # NOT a real spec (verify must agree with the compiler on a raw, un-normalized order).
    if isinstance(beh, dict) and any(str(beh.get(k, "") or "").strip()
                                     for k in _BEHAVIOR_ASPECTS):
        return True
    iface = c.get("interface")
    # An op is identified by `op` OR `name` -- mirror seasar_compile._normalize_interface,
    # which accepts either, so verify run on a raw (pre-normalization) order does not fail
    # this WARN on a valid name-keyed interface.
    return (isinstance(iface, list)
            and any(isinstance(o, dict)
                    and (str(o.get("op", "") or "").strip()
                         or str(o.get("name", "") or "").strip())
                    for o in iface))


def _wave_of(t):
    """Tolerant 1-based wave (mirrors seasar_compile._wave_of; kept local so this
    module has zero coupling to the compiler)."""
    try:
        return max(1, int(t.get("wave", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _tests_reference_fixture(order):
    for t in _dicts(order, "tasks"):
        blob = str(t.get("test", "")) + " " + str(t.get("acceptance", ""))
        if _FIXTURE_RE.search(blob):
            return True
    return False


def _fixture_materialized(f):
    """A fixture is materialized if it carries literal text `body`, or (for a binary
    input like an epub/pdf/image) `binary:true` + a reproducible `generator`."""
    if _nonempty(f.get("body")):
        return True
    if f.get("binary") and _nonempty(f.get("generator")):
        return True
    return False


def _scaffold_flags(order):
    """(has_manifest, has_test_ci, has_env) over the literal scaffold_files that carry a
    body -- the three things a runnable boot skeleton needs. A manifest is the floor: you
    cannot install/build without one."""
    paths = [str(s.get("path", "")).lower()
             for s in _dicts(order, "scaffold_files") if _nonempty(s.get("body"))]
    return (any(p.endswith(_MANIFESTS) for p in paths),
            any(any(h in p for h in _TEST_HINTS) for p in paths),
            any(p.endswith(_ENV_HINTS) for p in paths))


def _scaffold_score(order):
    """0-100 partial credit: of {manifest, test/CI config, env template}, how many the
    literal scaffold_files provide. 0 if there is no runnable skeleton at all."""
    return round(100 * sum(_scaffold_flags(order)) / 3)


# --- the graded executability factors (consumed by stamp) --------------------

def executability_factors(order):
    """The three 0-100 factors that grade whether the order is materialized executable
    artifact versus prose. Mirrors the WARN checks but as graded ratios for the score."""
    order = order if isinstance(order, dict) else {}
    contracts = _dicts(order, "contracts")
    if contracts:
        # Rubric per contract: literal `source` (the file agents IMPORT) weighted over a
        # `source_path` (where it lands). Rewards full materialization, not a non-empty
        # string -- a prose blurb with no landing file cannot score like a real contract.
        # This replaces the old count-based contract_coverage as the contract-depth signal.
        pts = sum((0.7 if _nonempty(c.get("source")) else 0.0)
                  + (0.3 if _nonempty(c.get("source_path")) else 0.0)
                  for c in contracts)
        contracts_compile = round(100 * pts / len(contracts))
    else:
        contracts_compile = 0

    fixtures = _dicts(order, "fixtures")
    if fixtures:
        mat = sum(1 for f in fixtures if _fixture_materialized(f))
        fixtures_materialized = round(100 * mat / len(fixtures))
    elif _tests_reference_fixture(order):
        fixtures_materialized = 0       # tests need fixtures but none are materialized
    else:
        fixtures_materialized = 100      # nothing to materialize -> satisfied

    return {
        "contracts_compile": contracts_compile,
        "fixtures_materialized": fixtures_materialized,
        "scaffold_runnable": _scaffold_score(order),
    }


# --- the full pass/fail verifier (consumed by the CLI + self_check) ----------

def verify_order(order):
    """Run every structural + executability check on an order. Returns:
        {ok, strict_ok, checks:[{name,ok,severity,detail}], summary, executability}
    `ok`         -- no ERROR check failed (the DAG is sound; safe to dispatch agents).
    `strict_ok`  -- ok AND no WARN check failed (the order is fully executable DNA).
    `executability` -- 0-100, the fraction of WARN checks passing.
    Never raises; a legacy order missing the new fields simply fails the WARN checks.
    """
    order = order if isinstance(order, dict) else {}
    checks = []

    def add(name, ok, severity, detail=""):
        checks.append({"name": name, "ok": bool(ok), "severity": severity,
                       "detail": "" if ok else str(detail)})

    tasks = _dicts(order, "tasks")
    # Stringify every id at the comparison boundary. verify_order runs on RAW orders too
    # (the CLI gate verify-build-order.py loads order JSON and does NOT normalize), so a
    # model that emits int task ids must still join to depends_on/consumers/owner_task,
    # which may arrive as either str or int. Mirror seasar_compile._normalize_order: one
    # key type (str) everywhere, so no edge is silently dropped and none phantom-dangles.
    task_ids = [_str_id(t.get("id")) for t in tasks if _str_id(t.get("id")) is not None]
    task_id_set = set(task_ids)

    # ---- STRUCTURAL (ERROR) -- DAG integrity ----
    add("tasks_present", len(tasks) > 0, ERROR, "no tasks defined")
    # Every task needs a non-empty id, or it escapes the id-keyed checks below
    # (waves_partition / deps / owner) and rides along, scheduled into no wave.
    add("tasks_have_ids", all(_nonempty(t.get("id")) for t in tasks), ERROR,
        "task(s) missing an id -- they escape every DAG check")
    seen_ids, dup_ids = set(), []
    for _i in task_ids:
        if _i in seen_ids and _i not in dup_ids:
            dup_ids.append(_i)
        seen_ids.add(_i)
    add("task_ids_unique", not dup_ids, ERROR,
        "duplicate task id(s): " + ", ".join(map(str, dup_ids)))

    by_wave = {}
    for t in tasks:
        by_wave.setdefault(_wave_of(t), []).append(t)
    collisions = []
    for wave, ts in by_wave.items():
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                shared = set(_strs(ts[i].get("files"))) & set(_strs(ts[j].get("files")))
                if shared:
                    collisions.append(
                        f"wave {wave}: {ts[i].get('id')} & {ts[j].get('id')} "
                        f"share {sorted(shared)}")
    add("wave_file_disjoint", not collisions, ERROR, "; ".join(collisions))

    dep_missing, dep_forward = [], []
    wave_by_id = {_str_id(t.get("id")): _wave_of(t) for t in tasks}
    for t in tasks:
        tw = _wave_of(t)
        tid = _str_id(t.get("id"))
        for d in _str_ids(t.get("depends_on")):
            if d not in task_id_set:
                dep_missing.append(f"{tid}->{d}")
            elif wave_by_id.get(d, 0) >= tw:
                dep_forward.append(f"{tid}(w{tw})->{d}(w{wave_by_id.get(d)})")
    add("deps_exist", not dep_missing, ERROR, "dangling: " + "; ".join(dep_missing))
    add("deps_point_backward", not dep_forward, ERROR,
        "not earlier-wave: " + "; ".join(dep_forward))

    waves = (order.get("orchestration") or {}).get("waves") or []
    # wave members stringify too -- a raw order may list int ids in orchestration.waves.
    flat = [s for w in waves for s in _str_ids(w)]
    add("waves_partition", sorted(flat) == sorted(task_ids), ERROR,
        "orchestration.waves does not list every task id exactly once")
    # Partition-by-set is not enough: the orchestrator schedules off orchestration.waves'
    # ORDER, so a task placed in too-early a group can run before a dependency even when
    # every id appears once. Validate the actual schedule respects deps.
    pos = {}
    for i, w in enumerate(waves):
        for tid in _str_ids(w):
            pos.setdefault(tid, i)
    sched_forward = []
    for t in tasks:
        tid = _str_id(t.get("id"))
        for d in _str_ids(t.get("depends_on")):
            if pos.get(tid) is not None and pos.get(d) is not None and pos[d] >= pos[tid]:
                sched_forward.append(f"{tid}<-{d}")
    add("waves_schedule_deps", not sched_forward, ERROR,
        "orchestration.waves schedules a task at/before a dependency: " + "; ".join(sched_forward))

    bad_owner = [c.get("name") for c in _dicts(order, "contracts")
                 if _str_id(c.get("owner_task")) is not None
                 and _str_id(c.get("owner_task")) not in task_id_set]
    add("contract_owner_exists", not bad_owner, ERROR,
        "owner_task not a task: " + ", ".join(map(str, bad_owner)))

    # ---- EXECUTABILITY (WARN) -- the DNA must be runnable artifact, not prose ----
    contracts = _dicts(order, "contracts")
    if contracts:
        no_src = [c.get("name") for c in contracts if not _nonempty(c.get("source"))]
        add("contracts_have_source", not no_src, WARN,
            "prose-only (no compilable source): " + ", ".join(map(str, no_src)))
        no_path = [c.get("name") for c in contracts
                   if _nonempty(c.get("source")) and not _nonempty(c.get("source_path"))]
        add("contracts_have_path", not no_path, WARN,
            "source without source_path: " + ", ".join(map(str, no_path)))
        # substance prober: contract source we can check in-process (python/json) must
        # actually parse, not just be non-empty -- a contract that looks like code but
        # does not compile is prose with extra steps.
        errs = [(c.get("name"), _source_parse_error(c)) for c in contracts]
        bad = ["%s: %s" % (n, e) for n, e in errs if e]
        add("contracts_source_parses", not bad, WARN,
            "contract source does not parse/compile: " + "; ".join(bad))
        # A typed seam pins the SHAPE; behavior (ordering/idempotency/errors/pagination/
        # units) or a language-neutral interface IR pins the SEMANTICS. A sourced contract
        # with neither is where two agents agree on types and silently diverge on behavior.
        no_beh = [c.get("name") for c in contracts
                  if _nonempty(c.get("source")) and not _has_behavior(c)]
        add("contracts_specify_behavior", not no_beh, WARN,
            "typed seam(s) with no behavioral spec or interface IR "
            "(semantic drift risk): " + ", ".join(map(str, no_beh)))
        # A declared consumer edge that points at no real task is a broken ripple: a
        # version bump would route its re-verify signal to nobody. Stringify both sides --
        # _str_ids (not _strs) so an int consumer id on a raw order is COMPARED, not dropped
        # (dropping would hide a real dangling edge AND a real valid one). Mirrors the
        # round-1 seasar_compile._contract_consumers fix.
        bad_consumers = ["%s->%s" % (c.get("name"), x) for c in contracts
                         for x in _str_ids(c.get("consumers")) if x not in task_id_set]
        add("consumers_are_tasks", not bad_consumers, WARN,
            "contract.consumers edge to a non-task (broken ripple): "
            + ", ".join(bad_consumers))
    else:
        add("contracts_have_source", False, WARN, "no contracts defined")

    fixtures = _dicts(order, "fixtures")
    refs_fixture = _tests_reference_fixture(order)
    if refs_fixture:
        add("fixtures_present_if_referenced", len(fixtures) > 0, WARN,
            "tasks reference fixtures but fixtures[] is empty")
    if fixtures:
        unmat = [f.get("path") for f in fixtures if not _fixture_materialized(f)]
        add("fixtures_materialized", not unmat, WARN,
            "no body/generator: " + ", ".join(map(str, unmat)))
    elif refs_fixture:
        add("fixtures_materialized", False, WARN,
            "fixtures referenced by tests but none materialized")

    add("scaffold_runnable", _scaffold_flags(order)[0], WARN,
        "scaffold_files lack a package manifest (nothing to install -- not a runnable skeleton)")

    gates = [g for g in _dicts(order, "quality_gates") if g.get("blocks_merge")]
    if gates:
        # require BOTH a test_source AND a test_path -- the exact pair the bundle needs to
        # emit a file; a gate with source but no path is a silently-evaporated predicate.
        no_pred = [g.get("name") for g in gates
                   if not (_nonempty(g.get("test_source")) and _nonempty(g.get("test_path")))]
        add("gates_have_predicates", not no_pred, WARN,
            "blocking gate(s) with no executable test_source+test_path (prose, not a gate): "
            + ", ".join(map(str, no_pred)))
        # a predicate that imports a fixture not in the bundle would fail to run.
        mat = {f.get("path") for f in _dicts(order, "fixtures") if _fixture_materialized(f)}
        miss = [f"{g.get('name')}:{r}" for g in gates
                for r in _strs(g.get("fixture_refs")) if r not in mat]
        add("gate_fixtures_materialized", not miss, WARN,
            "blocking gate(s) reference an unmaterialized fixture: " + ", ".join(map(str, miss)))
        # two gates writing one test_path -> the bundle drops one predicate.
        paths = [g.get("test_path") for g in gates
                 if _nonempty(g.get("test_source")) and _nonempty(g.get("test_path"))]
        dups = sorted({p for p in paths if paths.count(p) > 1})
        add("gate_predicates_distinct", not dups, WARN,
            "blocking gates share a test_path (one predicate is dropped at bundle time): "
            + ", ".join(map(str, dups)))

    # every decision must anchor to a real task/file, or it routes to NO agent's packet
    # while still blocking the build globally (a silent fleet deadlock).
    decisions = _dicts(order, "decisions")
    if decisions:
        all_files = {f for t in tasks for f in _strs(t.get("files"))}
        unrouted = [d.get("id") for d in decisions
                    if _str_id(d.get("anchor_task")) not in task_id_set
                    and d.get("anchor_file") not in all_files]
        add("decisions_routed", not unrouted, WARN,
            "decision(s) anchored to no real task/file (routes to no agent): "
            + ", ".join(map(str, unrouted)))

    orch = order.get("orchestration") or {}
    add("handoff_protocol_present", _nonempty(orch.get("handoff_protocol")), WARN,
        "orchestration.handoff_protocol is empty (no per-agent definition-of-done / merge order)")
    add("contract_evolution_present", _nonempty(orch.get("contract_evolution")), WARN,
        "orchestration.contract_evolution is empty (no propose-to-owner ritual for shared contracts)")

    work_orders = _dicts(order, "work_orders")
    if work_orders:
        no_dod = [w.get("agent") for w in work_orders
                  if not _nonempty(w.get("definition_of_done"))]
        add("work_orders_have_dod", not no_dod, WARN,
            "no definition_of_done: " + ", ".join(map(str, no_dod)))
    else:
        add("work_orders_have_dod", False, WARN, "no work_orders defined")

    errors = sum(1 for c in checks if c["severity"] == ERROR and not c["ok"])
    warnings = sum(1 for c in checks if c["severity"] == WARN and not c["ok"])
    warn_total = sum(1 for c in checks if c["severity"] == WARN)
    warn_pass = sum(1 for c in checks if c["severity"] == WARN and c["ok"])
    ok = errors == 0
    # The merge/handoff WARN checks NOT already graded by a dedicated score factor
    # (contracts_compile / fixtures_materialized / scaffold_runnable). stamp() folds
    # THIS into self_check_passes so the 8 score factors stay orthogonal.
    independent = ("handoff_protocol_present", "contract_evolution_present",
                   "work_orders_have_dod", "gates_have_predicates",
                   "gate_fixtures_materialized", "gate_predicates_distinct",
                   "decisions_routed", "contracts_specify_behavior")
    ind = [c for c in checks if c["name"] in independent]
    ind_pass = sum(1 for c in ind if c["ok"])
    return {
        "ok": ok,
        "strict_ok": ok and warnings == 0,
        "checks": checks,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "passed": sum(1 for c in checks if c["ok"]),
            "total": len(checks),
        },
        "executability": round(100 * warn_pass / max(1, warn_total)),
        "independent_executability": round(100 * ind_pass / max(1, len(ind))),
    }


def format_report(result, name=""):
    """Human-readable per-check report for the CLI. Pure string; no I/O."""
    head = "BUILD ORDER VERIFY" + (f"  {name}" if name else "")
    lines = [head, "=" * len(head)]
    for c in result["checks"]:
        mark = "PASS" if c["ok"] else ("FAIL" if c["severity"] == ERROR else "warn")
        line = f"  [{mark}] {c['name']}"
        if c["detail"]:
            line += f"  -- {c['detail']}"
        lines.append(line)
    s = result["summary"]
    lines += ["",
              f"  structural: {'OK' if result['ok'] else 'BROKEN'}"
              f"   |   executability: {result['executability']}/100"
              f"   |   {s['errors']} error(s), {s['warnings']} warning(s)"]
    return "\n".join(lines)
