"""Dispatch readiness helpers for latent requirements.

Readiness is stricter than "mentioned in a gate": a requirement can dispatch only
when it is accepted/satisfied and backed by a blocking gate whose Gate Forge
evidence discriminates, or when it is explicitly waived with a reason.
"""

import seasar_gate_forge
import seasar_requirements


READY_STATUSES = {"accepted", "satisfied"}


def _nonempty(v):
    return bool(str(v or "").strip())


def _dicts(order, key):
    order = order if isinstance(order, dict) else {}
    return [x for x in (order.get(key) or []) if isinstance(x, dict)]


def _requirements_raw(order):
    order = order if isinstance(order, dict) else {}
    if "latent_requirements" in order:
        return order.get("latent_requirements")
    if "requirements" in order:
        return order.get("requirements")
    return []


def _fixture_materialized(f):
    if _nonempty(f.get("body")):
        return True
    if f.get("binary") and _nonempty(f.get("generator")):
        return True
    return False


def _materialized_fixture_paths(order):
    return {f.get("path") for f in _dicts(order, "fixtures") if _fixture_materialized(f)}


def readiness_rows(order):
    """Return deterministic per-requirement dispatch-readiness rows."""
    order = order if isinstance(order, dict) else {}
    reqs = seasar_requirements.normalize_requirements(_requirements_raw(order))
    gates = [g for g in _dicts(order, "quality_gates") if g.get("blocks_merge")]
    gate_by_name = {str(g.get("name", "") or ""): g for g in gates}
    materialized = _materialized_fixture_paths(order)
    rows = []
    for req in reqs:
        status = req.get("status") or "open"
        gate_id = req.get("gate_id") or ""
        reasons = []
        gate = gate_by_name.get(gate_id)
        gate_state = "blocking" if gate else ("missing" if gate_id else "unspecified")
        forge = {}
        forge_status = "missing"
        if status == "waived":
            if not _nonempty(req.get("waiver_reason")):
                reasons.append("waiver_reason is required")
        else:
            if status not in READY_STATUSES:
                reasons.append("status is %s" % status)
            if not gate_id:
                reasons.append("gate_id is empty")
            elif not gate:
                reasons.append("blocking gate `%s` is missing" % gate_id)
            if gate:
                forge = seasar_gate_forge.normalize_gate_forge(
                    gate.get("gate_forge"),
                    gate_name=gate.get("name", ""),
                    test_path=gate.get("test_path", ""),
                    requirement_id=req.get("requirement_id", ""),
                    counter_cue=req.get("counter_cue", ""),
                )
                forge_status = forge.get("status") or "missing"
                problems = seasar_gate_forge.discrimination_problems(
                    forge, materialized_paths=materialized)
                if problems:
                    reasons.append("forge evidence: " + "; ".join(problems))
        rows.append({
            "requirement_id": req.get("requirement_id") or "",
            "affordance": req.get("affordance") or "",
            "status": status,
            "ready": not reasons,
            "gate_id": gate_id,
            "gate_state": gate_state,
            "forge_status": forge_status,
            "reasons": reasons,
            "waiver_reason": req.get("waiver_reason") or "",
            "counter_cue": req.get("counter_cue") or "",
            "source_span": req.get("source_span") or "",
        })
    return rows


def readiness_summary(order):
    rows = readiness_rows(order)
    ready = sum(1 for r in rows if r.get("ready"))
    return {"ready": ready, "total": len(rows), "rows": rows}


def readiness_text(row):
    if row.get("ready"):
        return "dispatch-ready"
    detail = "; ".join(row.get("reasons") or []) or "not dispatch-ready"
    return "not dispatch-ready: " + detail
