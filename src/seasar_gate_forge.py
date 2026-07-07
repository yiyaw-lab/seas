"""Gate Forge v0 metadata helpers.

This is the deterministic spine for agentic gate authoring: a quality gate can
carry the evidence that it was run against a golden fixture and a broken fixture.
The model may propose the gate, but Seasar only treats it as forged when the
normalized evidence says the gate passed golden and failed broken.
"""

import re


VALID_STATUSES = {"pending", "discriminates", "failed"}


def _slug(v):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(v or "").strip()).strip("-")
    return s.lower()


def _text(v):
    return str(v or "").strip()


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_attempt(raw, fallback_index=1, defaults=None):
    """Normalize one golden/broken gate run record."""
    raw = raw if isinstance(raw, dict) else {}
    defaults = defaults if isinstance(defaults, dict) else {}
    attempt = _int(raw.get("attempt"))
    if attempt is None or attempt < 1:
        attempt = fallback_index
    return {
        "attempt": attempt,
        "run_command": _text(raw.get("run_command") or defaults.get("run_command")),
        "test_path": _text(raw.get("test_path") or defaults.get("test_path")),
        "golden_fixture_ref": _text(
            raw.get("golden_fixture_ref") or defaults.get("golden_fixture_ref")),
        "golden_exit_code": _int(raw.get("golden_exit_code")),
        "broken_fixture_ref": _text(
            raw.get("broken_fixture_ref") or defaults.get("broken_fixture_ref")),
        "broken_exit_code": _int(raw.get("broken_exit_code")),
        "revision_note": _text(raw.get("revision_note")),
    }


def normalize_gate_forge(raw, gate_name="", test_path="", requirement_id="",
                         counter_cue=""):
    """Return a normalized gate_forge object, or {} when none was supplied."""
    if not isinstance(raw, dict) or not raw:
        return {}
    status = _text(raw.get("status") or "pending").lower()
    if status not in VALID_STATUSES:
        status = "pending"
    forge_id = _text(raw.get("forge_id"))
    if not forge_id and gate_name:
        forge_id = "forge-" + _slug(gate_name)
    defaults = {
        "run_command": raw.get("run_command"),
        "test_path": raw.get("test_path") or test_path,
        "golden_fixture_ref": raw.get("golden_fixture_ref"),
        "broken_fixture_ref": raw.get("broken_fixture_ref"),
    }
    raw_attempts = raw.get("attempts")
    if not isinstance(raw_attempts, list):
        raw_attempts = []
    if not raw_attempts and any(k in raw for k in (
            "golden_exit_code", "broken_exit_code",
            "golden_fixture_ref", "broken_fixture_ref")):
        raw_attempts = [raw]
    attempts = [
        normalize_attempt(a, i, defaults)
        for i, a in enumerate(raw_attempts, 1)
        if isinstance(a, dict)
    ]
    latest = attempts[-1] if attempts else {}
    out = {
        "forge_id": forge_id,
        "gate_id": _text(raw.get("gate_id") or gate_name),
        "requirement_id": _text(raw.get("requirement_id") or requirement_id),
        "counter_cue": _text(raw.get("counter_cue") or counter_cue),
        "status": status,
        "run_command": _text(raw.get("run_command") or latest.get("run_command")),
        "golden_fixture_ref": _text(
            raw.get("golden_fixture_ref") or latest.get("golden_fixture_ref")),
        "broken_fixture_ref": _text(
            raw.get("broken_fixture_ref") or latest.get("broken_fixture_ref")),
        "attempts": attempts,
        "failure_reason": _text(raw.get("failure_reason")),
    }
    return out


def fixture_refs(forge):
    f = normalize_gate_forge(forge)
    if not f:
        return []
    refs = []
    for key in ("golden_fixture_ref", "broken_fixture_ref"):
        if f.get(key):
            refs.append(f[key])
    for a in f.get("attempts") or []:
        for key in ("golden_fixture_ref", "broken_fixture_ref"):
            if a.get(key):
                refs.append(a[key])
    out, seen = [], set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _same_ref(left, right):
    left, right = _text(left), _text(right)
    return bool(left and right and left == right)


def discrimination_problems(forge, materialized_paths=None):
    """Return reasons a forge record does not prove gate discrimination."""
    f = normalize_gate_forge(forge)
    if not f:
        return ["missing gate_forge evidence"]
    problems = []
    if f["status"] != "discriminates":
        problems.append("status is %s" % (f["status"] or "missing"))
    if not f.get("run_command"):
        problems.append("run_command is empty")
    if not f.get("golden_fixture_ref"):
        problems.append("golden_fixture_ref is empty")
    if not f.get("broken_fixture_ref"):
        problems.append("broken_fixture_ref is empty")
    same_top_level_refs = _same_ref(f.get("golden_fixture_ref"),
                                    f.get("broken_fixture_ref"))
    if same_top_level_refs:
        problems.append("golden and broken fixture refs must differ")
    if materialized_paths is not None:
        missing = [r for r in fixture_refs(f) if r not in materialized_paths]
        if missing:
            problems.append("fixture(s) not materialized: " + ", ".join(missing))
    attempts = f.get("attempts") or []
    if not attempts:
        problems.append("no golden/broken run attempt recorded")
    else:
        latest = attempts[-1]
        if not same_top_level_refs and _same_ref(
                latest.get("golden_fixture_ref") or f.get("golden_fixture_ref"),
                latest.get("broken_fixture_ref") or f.get("broken_fixture_ref")):
            problems.append("latest attempt golden and broken fixture refs must differ")
        if latest.get("golden_exit_code") != 0:
            problems.append("golden fixture did not pass")
        b = latest.get("broken_exit_code")
        if b is None:
            problems.append("broken fixture was not run")
        elif b == 0:
            problems.append("broken fixture passed")
    return problems


def discriminates(forge, materialized_paths=None):
    return not discrimination_problems(forge, materialized_paths=materialized_paths)
