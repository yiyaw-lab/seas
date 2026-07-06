"""Deterministic latent-requirement scanner for Seasar build orders.

The scanner is deliberately narrow: it recognizes high-confidence post-round14
seams that have evidence today, emits one stable counter-cue per seam, and leaves
broader discovery to later Gate Forge work. Pure stdlib, no I/O, no model calls.
"""

import re


AFFORDANCE_ORDER = (
    "pagination",
    "caching",
    "retry",
    "debounce",
    "idempotency",
    "stale_reads",
    "async_events",
    "race_conditions",
)

_AFFORDANCES = {
    "pagination": {
        "requirement_id": "LR-PAGINATION-001",
        "confidence": 0.88,
        "gate_id": "gate-pagination-completeness",
        "patterns": (
            r"\bpaginat(?:e|ed|ion|ing)\b",
            r"\bnext[- ]page\b",
            r"\ball pages\b",
            r"\bpage (?:through|token|cursor|number|size|limit|offset)\b",
            r"\bcursor[-_ ]?(?:based|pagination|token)\b",
            r"\boffset[-_ ]?limit\b",
            r"\blist all (?:records|items|results|issues|events)\b",
            r"\ball (?:records|items|results|issues|events) across pages\b",
        ),
        "counter_cue": (
            "When a workflow reads a paginated collection, the implementation must "
            "continue until the source explicitly signals exhaustion and must never "
            "treat the first page as complete."
        ),
    },
    "caching": {
        "requirement_id": "LR-CACHING-001",
        "confidence": 0.86,
        "gate_id": "gate-cache-freshness",
        "patterns": (
            r"\bcach(?:e|ed|es|ing)\b",
            r"\bmemoiz(?:e|ed|es|ing|ation)\b",
            r"\bstale\b",
            r"\bttl\b",
            r"\btime[- ]to[- ]live\b",
        ),
        "counter_cue": (
            "When data is cached, the implementation must define the cache key, "
            "invalidation or TTL, and stale-read behavior, and must never let cached "
            "data hide the fresher source of truth."
        ),
    },
    "retry": {
        "requirement_id": "LR-RETRY-001",
        "confidence": 0.87,
        "gate_id": "gate-retry-idempotency",
        "patterns": (
            r"\bretr(?:y|ies|ied|ying)\b",
            r"\bexponential backoff\b",
            r"\bbackoff\b",
            r"\btransient (?:failure|error)\b",
            r"\brate[- ]limit(?:ed|ing)?\b",
            r"\bhttp 429\b",
            r"\bat[- ]least[- ]once\b",
        ),
        "counter_cue": (
            "When an operation may retry after a transient failure, the implementation "
            "must use an explicit retry budget plus an idempotency guard, and must "
            "never duplicate side effects after a partial failure."
        ),
    },
    "debounce": {
        "requirement_id": "LR-DEBOUNCE-001",
        "confidence": 0.85,
        "gate_id": "gate-debounce-final-action",
        "patterns": (
            r"\bdebounc(?:e|ed|es|ing)\b",
            r"\bthrottl(?:e|ed|es|ing)\b",
            r"\btypeahead\b",
            r"\bautocomplete\b",
            r"\bsearch[- ]as[- ]you[- ]type\b",
            r"\bkeystroke(?:s)?\b",
        ),
        "counter_cue": (
            "When input or events are debounced, the implementation must define "
            "leading or trailing behavior, cancellation, and flush timing, and must "
            "never drop the final intended action."
        ),
    },
    "idempotency": {
        "requirement_id": "LR-IDEMPOTENCY-001",
        "confidence": 0.84,
        "gate_id": "gate-idempotent-side-effects",
        "patterns": (
            r"\bidempot(?:ent|ency)\b",
            r"\bduplicate (?:request|event|message|payment|charge|side effect)s?\b",
            r"\bat[- ]most[- ]once\b",
            r"\bexactly[- ]once\b",
            r"\breplay(?:ed|able|ing)?\b",
            r"\bdeduplicat(?:e|ed|es|ing|ion)\b",
        ),
        "counter_cue": (
            "When an operation can be replayed or delivered more than once, the "
            "implementation must define an idempotency key or deduplication guard, "
            "and must never duplicate externally visible side effects."
        ),
    },
    "stale_reads": {
        "requirement_id": "LR-STALE-READS-001",
        "confidence": 0.83,
        "gate_id": "gate-stale-read-freshness",
        "patterns": (
            r"\bstale (?:read|reads|data|cache|response)s?\b",
            r"\beventual(?:ly)? consistent\b",
            r"\bread[- ]your[- ]writes\b",
            r"\breplica lag\b",
            r"\bfresh(?:ness)?\b",
            r"\bsource of truth\b",
        ),
        "counter_cue": (
            "When reads may be stale, the implementation must define the freshness "
            "boundary and source of truth, and must never treat stale replica or "
            "cache data as current where current data is required."
        ),
    },
    "async_events": {
        "requirement_id": "LR-ASYNC-EVENTS-001",
        "confidence": 0.82,
        "gate_id": "gate-async-event-completion",
        "patterns": (
            r"\basync(?:hronous)?\b",
            r"\bevent (?:bus|stream|handler|boundary|delivery)\b",
            r"\bwebhook (?:event|handler|callback|delivery)\b",
            r"\bqueue(?:d|s|ing)?\b",
            r"\bworker(?:s)?\b",
            r"\bbackground job(?:s)?\b",
        ),
        "counter_cue": (
            "When work crosses an asynchronous event boundary, the implementation "
            "must define delivery, ordering, failure handling, and completion "
            "visibility, and must never report completion before required async work "
            "is durably queued or processed."
        ),
    },
    "race_conditions": {
        "requirement_id": "LR-RACE-CONDITIONS-001",
        "confidence": 0.82,
        "gate_id": "gate-race-condition-serialization",
        "patterns": (
            r"\brace condition(?:s)?\b",
            r"\bconcurrent(?:ly| writes| requests| updates)?\b",
            r"\bparallel updates?\b",
            r"\blocking\b",
            r"\btransaction(?:al|s)?\b",
            r"\bmutex(?:es)?\b",
            r"\bcompare[- ]and[- ]swap\b",
        ),
        "counter_cue": (
            "When multiple actors can update the same state concurrently, the "
            "implementation must define serialization, locking, or conflict handling, "
            "and must never allow last-write-wins behavior to corrupt state."
        ),
    },
}

_SOURCE_PRIORITY = (
    "idea",
    "normalized_brief",
    "assumptions",
    "spec.what",
    "spec.why",
    "spec.acceptance_criteria",
    "spec.examples",
    "contracts",
    "tasks",
)

_VALID_STATUSES = {"open", "accepted", "satisfied", "waived"}


def _ordered_sources(sources):
    if not isinstance(sources, dict):
        return []
    seen = set()
    out = []
    for key in _SOURCE_PRIORITY:
        if key in sources:
            out.append((key, sources[key]))
            seen.add(key)
    for key in sorted(k for k in sources if k not in seen):
        out.append((key, sources[key]))
    return out


def _text(v):
    if isinstance(v, list):
        return "\n".join(_text(x) for x in v)
    if isinstance(v, dict):
        return "\n".join(_text(v[k]) for k in sorted(v))
    return str(v or "")


def _source_span(label, body, match):
    text = re.sub(r"\s+", " ", body).strip()
    if not text:
        return label
    start = max(0, match.start() - 48)
    end = min(len(body), match.end() + 72)
    snippet = re.sub(r"\s+", " ", body[start:end]).strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(body):
        snippet += "..."
    return f"{label}: {snippet}"


def _first_match(affordance, sources):
    cfg = _AFFORDANCES[affordance]
    compiled = [re.compile(p, re.I) for p in cfg["patterns"]]
    for label, raw in _ordered_sources(sources):
        body = _text(raw)
        if not body.strip():
            continue
        for pat in compiled:
            match = pat.search(body)
            if match:
                return _source_span(label, body, match)
    return None


def scan_sources(sources):
    """Return stable latent-requirement records for recognized affordances.

    `sources` is a label -> text/list/dict mapping. Output order is fixed by
    AFFORDANCE_ORDER, not by source order, so repeated scans are byte-stable.
    """
    reqs = []
    for affordance in AFFORDANCE_ORDER:
        span = _first_match(affordance, sources)
        if not span:
            continue
        cfg = _AFFORDANCES[affordance]
        reqs.append({
            "requirement_id": cfg["requirement_id"],
            "source_span": span,
            "affordance": affordance,
            "counter_cue": cfg["counter_cue"],
            "confidence": cfg["confidence"],
            "evidence_type": "affordance_scan",
            "gate_id": cfg["gate_id"],
            "status": "open",
            "waiver_reason": "",
        })
    return reqs


def sources_from_order(order, idea="", brief=None):
    """Collect scanner text from the final order without assuming a complete schema."""
    order = order if isinstance(order, dict) else {}
    brief = brief if isinstance(brief, dict) else {}
    spec = order.get("spec") if isinstance(order.get("spec"), dict) else {}
    sources = {
        "idea": idea or order.get("idea", ""),
        "normalized_brief": brief.get("normalized_idea", ""),
        "assumptions": brief.get("assumptions", []),
        "spec.what": spec.get("what", ""),
        "spec.why": spec.get("why", ""),
        "spec.acceptance_criteria": spec.get("acceptance_criteria", []),
        "spec.examples": spec.get("examples", []),
        "contracts": [
            {
                "name": c.get("name", ""),
                "detail": c.get("detail", ""),
                "behavior": c.get("behavior", {}),
                "interface": c.get("interface", []),
            }
            for c in (order.get("contracts") or []) if isinstance(c, dict)
        ],
        "tasks": [
            {
                "title": t.get("title", ""),
                "test": t.get("test", ""),
                "acceptance": t.get("acceptance", ""),
            }
            for t in (order.get("tasks") or []) if isinstance(t, dict)
        ],
    }
    return sources


def scan_order(order, idea="", brief=None):
    return scan_sources(sources_from_order(order, idea=idea, brief=brief))


def _confidence(v, default=0.0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = default
    return max(0.0, min(1.0, n))


def normalize_requirement(req, fallback_index=1):
    req = req if isinstance(req, dict) else {}
    affordance = str(req.get("affordance", "") or "").strip().lower()
    cfg = _AFFORDANCES.get(affordance, {})
    rid = str(req.get("requirement_id") or req.get("id")
              or cfg.get("requirement_id") or f"LR-CUSTOM-{fallback_index:03d}")
    rid = re.sub(r"[^A-Za-z0-9_-]", "", rid).strip() or f"LR-CUSTOM-{fallback_index:03d}"
    status = str(req.get("status") or "open").strip().lower()
    if status not in _VALID_STATUSES:
        status = "open"
    return {
        "requirement_id": rid,
        "source_span": str(req.get("source_span", "") or "").strip(),
        "affordance": affordance,
        "counter_cue": str(req.get("counter_cue") or cfg.get("counter_cue") or "").strip(),
        "confidence": _confidence(req.get("confidence"), cfg.get("confidence", 0.0)),
        "evidence_type": str(req.get("evidence_type") or "compiler").strip(),
        "gate_id": str(req.get("gate_id") or cfg.get("gate_id") or "").strip(),
        "status": status,
        "waiver_reason": str(req.get("waiver_reason", "") or "").strip(),
    }


def normalize_requirements(reqs):
    if isinstance(reqs, dict):
        reqs = [reqs]
    if not isinstance(reqs, list):
        return []
    out = []
    seen = set()
    for i, req in enumerate(reqs, 1):
        if not isinstance(req, dict):
            continue
        n = normalize_requirement(req, i)
        key = n["requirement_id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def merge_requirements(*groups):
    """Merge scanner and model requirements, keeping scanner counter-cues canonical."""
    merged = []
    by_key = {}
    for group in groups:
        for req in normalize_requirements(group):
            key = req["affordance"] if req["affordance"] in _AFFORDANCES else req["requirement_id"]
            if key in by_key:
                existing = merged[by_key[key]]
                for field in ("source_span", "gate_id", "waiver_reason"):
                    if not existing.get(field) and req.get(field):
                        existing[field] = req[field]
                if existing.get("status") == "open" and req.get("status") != "open":
                    existing["status"] = req["status"]
                continue
            by_key[key] = len(merged)
            merged.append(req)
    return merged


def prompt_block(reqs):
    reqs = normalize_requirements(reqs)
    if not reqs:
        return "(none detected)"
    lines = []
    for r in reqs:
        lines.append(
            "- {rid} ({affordance}, confidence {confidence:.2f}, suggested gate `{gate}`): "
            "{cue}\n  Trigger: {span}".format(
                rid=r["requirement_id"],
                affordance=r["affordance"] or "unknown",
                confidence=r["confidence"],
                gate=r["gate_id"] or "(none)",
                cue=r["counter_cue"],
                span=r["source_span"] or "(compiler-supplied)",
            )
        )
    return "\n".join(lines)
