"""
SEAS V3 — the Finding schema and its emission gate (the contract).

A "genuine finding" is the thing the old pipeline could never produce: a claim
grounded in something the system did not already know going in. This module is
the single place that DEFINES what a finding must contain and ENFORCES it. Both
the world model (which turns a finding into a belief) and the synthesis floor
(seas_finding.py, which produces findings) import this — one schema, one gate,
two call sites.

The gate is the whole point. It is what stops SEAS from laundering a signal's
own description back out as finding-shaped text (which is exactly what legacy
F-001 is). A finding with no external evidence and no falsifiable dated
prediction is REJECTED at emission, before it can ever reach findings/.

Pure + standard-library only: no I/O, no LLM, no network. Fully unit-testable
without any API key.
"""

from datetime import datetime

# A finding must carry at least this much external grounding to be emitted.
MIN_EVIDENCE = 2          # >=2 evidence items...
MIN_SOURCE_EVIDENCE = 1   # ...at least one of which is an external source/artifact

# Confidence a synthesis finding is allowed to enter the world model at. Latent-
# in-sources is weak ground, so it starts low and must EARN its way up via a
# scored prediction. Asserted confidence is banned (see world_model.py).
SYNTHESIS_SEED_CONFIDENCE = 0.3

VALID_METHODS = ("synthesis", "experiment")
VALID_EVIDENCE_KINDS = ("source", "artifact")


def new_finding(
    finding_id, claim, method, evidence, prediction,
    refutation_condition, extends=None, contradicts=None,
    confidence=SYNTHESIS_SEED_CONFIDENCE, date=None,
):
    """Construct a finding dict in the V3 schema. Does NOT validate — call
    validate_finding() before persisting. Kept separate so a caller can build a
    draft, then gate it."""
    return {
        "id": finding_id,
        "claim": claim,
        "method": method,
        "evidence": evidence or [],
        "prediction": prediction,
        "refutation_condition": refutation_condition,
        "extends": extends or [],
        "contradicts": contradicts or [],
        "confidence": confidence,
        "status": "unverified",
        "date": date or datetime.now().strftime("%Y-%m-%d"),
    }


def _check_prediction(pred):
    """A prediction must be falsifiable: a claim, a resolve date, and a
    mechanically checkable fact. Returns a list of problems (empty == ok)."""
    problems = []
    if not isinstance(pred, dict):
        return ["prediction is missing or not an object"]
    if not pred.get("claim"):
        problems.append("prediction.claim is empty")
    resolves = pred.get("resolves")
    if not resolves:
        problems.append("prediction.resolves (a date) is missing")
    else:
        try:
            datetime.strptime(resolves, "%Y-%m-%d")
        except (ValueError, TypeError):
            problems.append(f"prediction.resolves '{resolves}' is not YYYY-MM-DD")
    if not pred.get("checkable"):
        problems.append("prediction.checkable (a fetchable fact) is missing")
    return problems


def _check_evidence(evidence):
    """Evidence must exist, be the right shape, and include at least one external
    source/artifact (not just internal cross-references)."""
    problems = []
    if not isinstance(evidence, list) or len(evidence) < MIN_EVIDENCE:
        problems.append(f"need >={MIN_EVIDENCE} evidence items, got "
                        f"{len(evidence) if isinstance(evidence, list) else 0}")
        return problems  # can't check shape of a non-list
    source_like = 0
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict):
            problems.append(f"evidence[{i}] is not an object")
            continue
        kind = ev.get("kind")
        if kind not in VALID_EVIDENCE_KINDS:
            problems.append(f"evidence[{i}].kind '{kind}' invalid "
                            f"(want {VALID_EVIDENCE_KINDS})")
        if kind == "source" and not ev.get("url"):
            problems.append(f"evidence[{i}] is a source with no url")
        if kind == "artifact" and not ev.get("path"):
            problems.append(f"evidence[{i}] is an artifact with no path")
        if kind in VALID_EVIDENCE_KINDS:
            source_like += 1
    if source_like < MIN_SOURCE_EVIDENCE:
        problems.append(f"need >={MIN_SOURCE_EVIDENCE} external source/artifact "
                        "evidence item(s)")
    return problems


def _check_quote_fidelity(finding, sources):
    """Verify cited quotes are REAL substrings of the fetched source text. The
    benchmark caught a model passing the structural gate while ~75% of its quotes
    were fabricated/paraphrased — citing a source it didn't actually support the
    claim from. Structure (is there a quote?) isn't enough; fidelity (is the
    quote real?) is what makes evidence trustworthy.

    `sources` is a list of {"url","text"}. A quote counts as verified if a short
    window of it appears in its cited source (or, lenient, in any source). Only
    runs when sources are supplied — omit them to keep the old structural-only
    behavior (existing callers/tests).
    """
    text_by_url = {s["url"]: s.get("text", "") for s in sources}
    all_text = " ".join(text_by_url.values()).lower()
    problems = []
    for i, ev in enumerate(finding.get("evidence", [])):
        if not isinstance(ev, dict) or ev.get("kind") != "source":
            continue
        quote = (ev.get("quote") or "").strip()
        if not quote:
            continue  # a source cited without a quote is allowed; nothing to verify
        body = (text_by_url.get(ev.get("url", ""), "") or "").lower()
        probe = quote[:40].lower()
        if probe not in body and probe not in all_text:
            problems.append(
                f"evidence[{i}] quote not found in its source (possible "
                f"fabrication): \"{quote[:50]}...\"")
    return problems


def validate_finding(finding, sources=None):
    """Apply the emission gate. Returns (ok: bool, problems: list[str]).

    This is the rule that makes a finding 'genuine': external cited evidence AND
    a falsifiable dated prediction AND a stated refutation condition — AND, when
    `sources` are supplied, that the cited quotes are REAL (not fabricated). A
    draft that fails this never becomes a finding; the caller emits a PROBE
    instead (see probes.py).

    `sources` (optional): list of {"url","text"} the finding was synthesized
    from. Supply it to enforce quote fidelity; omit it for structure-only checks.
    """
    problems = []
    if not finding.get("claim"):
        problems.append("claim is empty")
    if finding.get("method") not in VALID_METHODS:
        problems.append(f"method '{finding.get('method')}' invalid "
                        f"(want {VALID_METHODS})")
    if not finding.get("refutation_condition"):
        problems.append("refutation_condition is missing (what would kill this?)")
    problems += _check_evidence(finding.get("evidence", []))
    problems += _check_prediction(finding.get("prediction"))
    if sources:
        problems += _check_quote_fidelity(finding, sources)
    return (not problems, problems)
