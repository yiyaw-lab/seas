"""
SEAS V3 — the world model: beliefs Argo holds about the frontier.

This is the data structure the vision calls "continuously updates its world
model." A belief is what a finding becomes once it enters Argo's standing view
of the frontier: a claim with a confidence that is EARNED, plus the evidence and
scored predictions behind it. The world model is what ties findings, signals,
insights and predictions together over time — the thing V1/V2 never had.

The one inviolable rule: **confidence moves ONLY via evidence or a scored
prediction, never by assertion.** That is enforced structurally here — the only
public mutators are add_evidence() and apply_prediction_outcome(). There is no
set_confidence(). If you find yourself wanting one, you are about to launder a
hunch into a confidence number, which is the exact failure (legacy F-001) this
whole design exists to prevent.

Honest seeding: legacy F-001 enters at low confidence, status 'unverified',
because it has no artifact and no scored prediction behind it. It must earn its
way up like everything else.

Standard-library only. JSON store at data/world_model.json.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import argo_paths

ROOT = Path(__file__).resolve().parent.parent
# Env-overridable like the other live stores (argo_paths): the webhook and the
# evolution loop write beliefs at runtime, so point ARGO_WORLD_MODEL_PATH at the
# Railway volume or each redeploy resets to the committed copy.
WORLD_MODEL_PATH = Path(os.environ.get("ARGO_WORLD_MODEL_PATH",
                                       str(ROOT / "data" / "world_model.json")))
FINDINGS_DIR = argo_paths.FINDINGS_DIR  # single source of truth (see argo_paths)

# Confidence is clamped to an open interval: a belief is never certain (1.0) and
# never fully dead (0.0 -> it gets retired to 'refuted' status instead).
CONF_MIN, CONF_MAX = 0.05, 0.95

# How much a single piece of evidence / a scored prediction moves confidence.
# Predictions (reality graded them) move it more than evidence (someone argued
# for it). Deliberately coarse: this early, finer math would be false precision.
EVIDENCE_STEP = 0.05
PREDICTION_STEP = 0.20

SEED_CONFIDENCE = 0.3  # legacy findings enter here (unverified)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load():
    if not WORLD_MODEL_PATH.exists():
        return []
    try:
        return json.loads(WORLD_MODEL_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _save(beliefs):
    WORLD_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORLD_MODEL_PATH.write_text(json.dumps(beliefs, indent=2) + "\n")


def _clamp(c):
    return max(CONF_MIN, min(CONF_MAX, c))


def get_beliefs(status=None):
    """Return all beliefs, optionally filtered by status."""
    beliefs = _load()
    if status:
        return [b for b in beliefs if b.get("status") == status]
    return beliefs


def get_belief(belief_id):
    return next((b for b in _load() if b.get("id") == belief_id), None)


def _next_id(beliefs):
    n = 1 + max((int(b["id"].split("-")[1]) for b in beliefs
                 if b.get("id", "").startswith("WM-")), default=0)
    return f"WM-{n:03d}"


def add_belief(claim, confidence=SEED_CONFIDENCE, evidence=None,
               status="unverified", source_finding=None):
    """Create a new belief. Confidence defaults to the low seed (must be earned
    up). Returns the new belief's id. Idempotent-ish: if an identical claim
    already exists, returns that one instead of duplicating."""
    beliefs = _load()
    existing = next((b for b in beliefs if b.get("claim") == claim), None)
    if existing:
        return existing["id"]
    bid = _next_id(beliefs)
    beliefs.append({
        "id": bid,
        "claim": claim,
        "confidence": _clamp(confidence),
        "evidence": list(evidence or ([source_finding] if source_finding else [])),
        "refutations": [],
        "predictions": [],
        "status": status,
        "last_updated": _now(),
    })
    _save(beliefs)
    return bid


def add_evidence(belief_id, ref, supports=True):
    """Record evidence for/against a belief and nudge confidence accordingly.
    `ref` is a finding id, signal ref, or source URL. This is ONE of only two
    legitimate ways confidence moves. Returns the updated belief, or None."""
    beliefs = _load()
    b = next((x for x in beliefs if x.get("id") == belief_id), None)
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
    _save(beliefs)
    return b


def apply_prediction_outcome(belief_id, prediction_id, correct):
    """Reality scored a prediction tied to this belief. This is the STRONGEST
    (and the only other) way confidence moves — an external grader, not the
    model. A correct prediction raises confidence and can promote toward a
    theory; a wrong one lowers it and can refute the belief. Returns the updated
    belief, or None."""
    beliefs = _load()
    b = next((x for x in beliefs if x.get("id") == belief_id), None)
    if b is None:
        return None
    if prediction_id not in b.setdefault("predictions", []):
        b["predictions"].append(prediction_id)
    if correct:
        b["confidence"] = _clamp(b["confidence"] + PREDICTION_STEP)
        if b["confidence"] >= CONF_MAX - 1e-9:
            b["status"] = "promoted-to-theory"
        elif b["status"] == "unverified":
            b["status"] = "active"  # a scored prediction earns it out of unverified
    else:
        b["confidence"] = _clamp(b["confidence"] - PREDICTION_STEP)
        b.setdefault("refutations", []).append(f"prediction:{prediction_id}:wrong")
        if b["confidence"] <= CONF_MIN + 1e-9:
            b["status"] = "refuted"
        else:
            b["status"] = "weakening"
    b["last_updated"] = _now()
    _save(beliefs)
    return b


def seed_from_findings():
    """Bootstrap the world model from existing findings/ files (honestly).

    Legacy prose findings (e.g. F-001) have no artifact and no scored prediction,
    so they enter at SEED_CONFIDENCE / 'unverified' and must earn their way up.
    New schema findings (JSON) carry their own confidence/evidence. Idempotent:
    re-running won't duplicate a belief whose source finding is already recorded.
    Returns the list of belief ids touched.
    """
    if not FINDINGS_DIR.exists():
        return []
    touched = []
    for path in sorted(FINDINGS_DIR.glob("F-*")):
        fid = path.stem.split("-")[0] + "-" + path.stem.split("-")[1]  # 'F-001'
        # Already seeded? (a belief citing this finding id as evidence)
        if any(fid in b.get("evidence", []) for b in _load()):
            continue
        if path.suffix == ".json":
            try:
                f = json.loads(path.read_text())
            except (json.JSONDecodeError, ValueError):
                continue
            bid = add_belief(
                claim=f.get("claim", path.stem),
                confidence=f.get("confidence", SEED_CONFIDENCE),
                status=f.get("status", "unverified"),
                source_finding=fid,
            )
        else:
            # Prose finding: pull the first heading as the claim, seed low.
            claim = path.stem
            for ln in path.read_text().splitlines():
                if ln.strip().startswith("#"):
                    claim = ln.lstrip("# ").strip()
                    break
            bid = add_belief(claim=claim, confidence=SEED_CONFIDENCE,
                             status="unverified", source_finding=fid)
        touched.append(bid)
    return touched


def format_beliefs_for_prompt(limit=10):
    """A compact, human-readable belief list for feeding into a prompt (the
    critic reads this to check an insight against what Argo currently holds).
    Highest-confidence first."""
    beliefs = sorted(_load(), key=lambda b: b.get("confidence", 0), reverse=True)
    if not beliefs:
        return "(no beliefs yet)"
    lines = []
    for b in beliefs[:limit]:
        lines.append(f"{b['id']} [{b['confidence']:.2f} {b['status']}]: {b['claim']}")
    return "\n".join(lines)
