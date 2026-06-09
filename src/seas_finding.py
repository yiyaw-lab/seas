"""
SEAS V3 — Stage 1: the evidence-synthesis floor.

This replaces the dead end where the old pipeline stopped (a templated
experiment CARD that nothing ran). It produces a genuine finding, or an honest
dead-end probe, by GROUNDING in real sources:

    opportunity (a scored signal with a link)
      -> dedup check (probes.should_investigate)
      -> fetch N>=2 real sources (the signal's link + related signals' links)
      -> model judges ONLY cross-source convergence / contradiction (structured)
      -> if it proposes a finding: build via seas_schema -> run the EMISSION GATE
           gate pass  -> write finding JSON + seed/strengthen a world-model belief
           gate fail  -> probe 'inconclusive' (the model over-claimed; no real ground)
      -> if it says too-thin                 -> probe 'premature'
      -> if sources couldn't be fetched      -> probe 'unreachable' + ledger/escalation

The model PROPOSES; the deterministic gate (seas_schema.validate_finding)
DISPOSES. That split is what keeps a confident-sounding LLM from laundering a
signal back out as a "finding" — the same discipline as the V3 critic.

This does NOT execute code (that is Stage 2, the executed-experiment ceiling,
designed separately). The honest ceiling of synthesis is "N independent sources
say X, here are the links" — never "I ran X".

Reuses, does not rebuild:
  - argo_observe: provider routing + guarded model call (budget/breaker/retry);
  - argo_mcp_server._to_text + fetch_signals._fetch_url: the fetch + readable-text
    path (same allowlist-free direct fetch the pipeline already uses for feeds);
  - seas_schema: the finding schema + emission gate;
  - world_model / probes: the stores.

Run:  python src/seas_finding.py            (investigate the top opportunity)
      python src/seas_finding.py --dry-run  (fetch + judge + print, write nothing)
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import argo_observe as observe
import fetch_signals
import firecrawl_client
import probes
import seas_schema
import world_model

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = ROOT / "data" / "signals.json"
OPPORTUNITIES_PATH = ROOT / "data" / "opportunities.json"
FINDINGS_DIR = ROOT / "findings"
RUNS_DIR = ROOT / "runs"

MIN_SOURCES = 2            # need at least this many fetched sources to synthesize
SCORE_PROMPT_PATH = ROOT / "prompts" / "score_signal.md"
MAX_SOURCE_CHARS = 6000    # per source, fed to the model
DEFAULT_RESOLVE_DAYS = 90  # default horizon if the model omits one


# --- source fetching (records into the failure ledger) ----------------------

def _fetch_source(url):
    """Fetch one source's readable text. On failure, record it in the ledger and
    return None. Reuses the pipeline's own fetch + text-extraction."""
    import argo_mcp_server  # for _to_text; lazy to avoid import cost when unused
    try:
        raw = fetch_signals._fetch_url(url, timeout=10)
    except Exception as exc:
        status = _http_status(exc)
        probes.record_fetch_failure(url, status)
        print(f"  ! fetch failed [{status}]: {url}")
        return None
    probes.record_fetch_success(url)
    return argo_mcp_server._to_text(raw)[:MAX_SOURCE_CHARS]


def _http_status(exc):
    """Pull an HTTP status code out of an exception, or None (timeout/DNS)."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(m.group(1)) if m else None


# --- the synthesis prompt (structured output the gate can route) ------------

SYSTEM = (
    "You are SEAS, a frontier research engine. You do not speculate. You make a "
    "claim ONLY when independent sources actually support it. Your output is "
    "evidence-grounded or it is nothing."
)

INSTRUCTIONS = """You are given a frontier OPPORTUNITY and the readable text of
several real SOURCES about it. Find whether there is a genuine, defensible
FINDING here — a claim grounded in CROSS-SOURCE convergence or contradiction,
not a restatement of any single source.

Be strict. Most of the time the honest answer is "no finding yet". Refuse to
manufacture one.

Return ONLY a JSON object (no prose, no code fences) of this shape:

{
  "verdict": "finding" | "premature" | "inconclusive",
  "reason": "<one sentence: why this verdict>",
  "claim": "<if finding: one falsifiable sentence; else empty>",
  "evidence": [
    {"kind":"source","url":"<one of the source urls>","quote":"<short real quote>","stance":"supports|contradicts"}
  ],
  "prediction": {"claim":"<a dated, checkable prediction the claim implies>","resolves":"YYYY-MM-DD","checkable":"<the fact that would settle it>"},
  "refutation_condition": "<what observation would prove the claim wrong>",
  "confidence": <0.0-1.0>
}

Rules:
- "finding" REQUIRES >=2 source evidence items from DIFFERENT sources, a
  falsifiable dated prediction, and a refutation condition. If you cannot supply
  all three from the real sources, you must NOT use "finding".
- "premature": the topic is real but too nascent / under-sourced to ground a claim.
- "inconclusive": sources are adequate but show no load-bearing pattern.
- Quotes must be real substrings of the provided sources. Do not invent urls.
"""


def _extract_json(text):
    """Pull the first JSON object out of a model reply (tolerates fences/prose)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        raw = text[start:end + 1] if (start != -1 and end > start) else None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


# --- opportunity selection ---------------------------------------------------

def _load_opportunity():
    """Pick the signal to investigate: the top qualifying opportunity if scored,
    else the first signal that has a link. Related sources are no longer pulled
    from sibling signals (they share no topic) — they come from a topical search
    in _gather_sources. Returns the chosen signal, or None."""
    signals = json.loads(SIGNALS_PATH.read_text()) if SIGNALS_PATH.exists() else []
    signals = [s for s in signals if s.get("link")]
    if not signals:
        return None
    chosen = signals[0]
    if OPPORTUNITIES_PATH.exists():
        try:
            opps = json.loads(OPPORTUNITIES_PATH.read_text())
            winner = next((o for o in opps if o.get("qualifies")), None)
            if winner:
                match = next((s for s in signals
                              if s["title"] == winner["title"]), None)
                if match:
                    chosen = match
        except (json.JSONDecodeError, ValueError):
            pass
    return chosen


def _signal_ref(signal):
    return "signal:" + re.sub(r"[^a-z0-9]+", "-", signal["title"].lower())[:50]


def _search_category(signal):
    """Map a signal's source to a Firecrawl search category for topical search."""
    src = (signal.get("source") or "").lower()
    if src.startswith("arxiv"):
        return "research"
    if "github" in src:
        return "github"
    return None  # company/blog -> general web search


def _search_terms(signal):
    """A search query for finding sources on the SAME topic as this signal.
    Uses the title (the densest topic signal); drops arXiv id noise."""
    title = re.sub(r"arXiv:\S+", "", signal.get("title", "")).strip()
    return title[:200]


def _gather_sources(signal, dry_run=False):
    """Build a TOPICAL pool of sources to synthesize over. This is the fix for
    the clustering gap: instead of reusing random sibling signals (which share
    no topic), we (1) read the signal's own source, then (2) SEARCH for sources
    about the same topic via Firecrawl. Convergence needs sources about one
    thing; a stratified-diverse signal batch never had that.

    Falls back to stdlib when Firecrawl is unavailable: at minimum we still get
    the signal's own source, and the synthesis floor will honestly probe
    'premature' (one source, no convergence) rather than fake a finding.
    Returns a list of {"url","title","text"}.
    """
    import argo_mcp_server  # for the ALLOWED_HOSTS allowlist
    sources = []

    # (1) the signal's own source page.
    own = _fetch_source(signal["link"])
    if own:
        sources.append({"url": signal["link"],
                        "title": signal.get("title", ""), "text": own})

    # (2) topically-related sources via Firecrawl search (the keystone).
    related = firecrawl_client.search_related(
        _search_terms(signal),
        category=_search_category(signal),
        limit=5,
        allowed_hosts=argo_mcp_server.ALLOWED_HOSTS,
    )
    if related:
        have = {s["url"] for s in sources}
        for r in related:
            if r["url"] not in have:
                sources.append(r)
                have.add(r["url"])
    elif related is None and not dry_run:
        # Firecrawl unavailable. We proceed with whatever we have (often just the
        # signal's own source) — the gate will honestly probe premature.
        print("  (firecrawl unavailable — synthesizing on the signal's source "
              "alone; expect a 'premature' probe)")
    return sources


# --- the run -----------------------------------------------------------------

def investigate(signal, dry_run=False):
    """Investigate one opportunity end-to-end. Returns a status string."""
    sig_ref = _signal_ref(signal)

    ok, why = probes.should_investigate(sig_ref)
    if not ok and not dry_run:
        return f"Skipped {sig_ref}: {why}"

    # Gather a TOPICAL pool: the signal's own source + Firecrawl-searched related
    # sources on the same topic. This is the clustering-gap fix (see
    # _gather_sources): convergence needs sources about ONE thing.
    sources = _gather_sources(signal, dry_run=dry_run)

    if len(sources) < MIN_SOURCES:
        # Too thin to look for cross-source convergence. Distinguish the cause:
        # if the signal's own source failed to fetch, that's a tooling problem
        # (unreachable -> failure ledger); otherwise the topic is just under-
        # sourced right now (premature).
        own_failed = not any(s["url"] == signal["link"] for s in sources)
        reason = ("signal's own source unreachable" if own_failed
                  else "no related sources found to corroborate against")
        if not dry_run:
            outcome = "unreachable" if own_failed else "premature"
            probes.record_probe(sig_ref, outcome, [s["url"] for s in sources],
                                reason, cost={"sources": len(sources)})
            if own_failed:
                _maybe_escalate([signal["link"]])
            return f"No finding ({outcome}): {reason}"
        return f"[dry-run] would probe ({len(sources)} sources): {reason}"

    # Build the synthesis job and call the model (guarded inside argo_observe).
    sources_block = "\n\n".join(
        f"SOURCE {i+1} ({s['url']}):\n{s['text']}" for i, s in enumerate(sources))
    job = (f"{INSTRUCTIONS}\n\n## OPPORTUNITY\n{signal['title']}\n"
           f"{signal.get('summary','')}\n\n## SOURCES\n{sources_block}")

    import os
    models = [m for m in observe.resolve_models()
              if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])]
    if not models:
        return "No API key available; cannot synthesize."
    reply = None
    for model in models:
        try:
            # generate_observations is the guarded string-in/string-out call
            # (budget/breaker/retry). Prepend SYSTEM so the engine stays strict.
            reply = observe.generate_observations(f"{SYSTEM}\n\n{job}", model)
            break
        except Exception as exc:
            print(f"  ! {model}: {exc}")
    if reply is None:
        return "Synthesis model call failed."

    judgment = _extract_json(reply)
    if judgment is None:
        return f"Could not parse model judgment. Raw head: {reply[:200]}"

    return _route_judgment(signal, sig_ref, sources, judgment, dry_run)


def _route_judgment(signal, sig_ref, sources, judgment, dry_run):
    """The model proposed; now the deterministic gate disposes."""
    verdict = judgment.get("verdict")

    if verdict != "finding":
        outcome = "premature" if verdict == "premature" else "inconclusive"
        why = judgment.get("reason", "no finding")
        if dry_run:
            return f"[dry-run] verdict={verdict} -> probe {outcome}: {why}"
        probes.record_probe(sig_ref, outcome, [s["url"] for s in sources], why,
                           cost={"fetches": len(sources), "model_calls": 1})
        return f"No finding ({outcome}): {why}"

    # Model claims a finding — BUILD it and run the emission gate.
    pred = judgment.get("prediction") or {}
    if pred and not pred.get("resolves"):
        pred["resolves"] = (datetime.now() + timedelta(days=DEFAULT_RESOLVE_DAYS)
                            ).strftime("%Y-%m-%d")
    fid = _next_finding_id()
    draft = seas_schema.new_finding(
        finding_id=fid,
        claim=judgment.get("claim", ""),
        method="synthesis",
        evidence=judgment.get("evidence", []),
        prediction=pred,
        refutation_condition=judgment.get("refutation_condition", ""),
        confidence=min(seas_schema.SYNTHESIS_SEED_CONFIDENCE,
                       float(judgment.get("confidence", 0.3) or 0.3)),
    )
    # Pass sources so the gate also verifies quote FIDELITY (cited quotes must be
    # real substrings of the fetched sources), not just structure.
    passed, problems = seas_schema.validate_finding(draft, sources=sources)

    if not passed:
        # The model over-claimed: it said "finding" but the evidence/prediction
        # don't actually clear the bar. That is an inconclusive probe, not a
        # finding. This is the gate doing its job.
        why = "model claimed a finding but it failed the emission gate: " + \
              "; ".join(problems)
        if dry_run:
            return f"[dry-run] GATE REJECTED -> probe inconclusive: {why}"
        probes.record_probe(sig_ref, "inconclusive", [s["url"] for s in sources],
                           why, cost={"fetches": len(sources), "model_calls": 1})
        return f"No finding (gate rejected): {why}"

    if dry_run:
        return ("[dry-run] GATE PASSED -> would write finding:\n"
                + json.dumps(draft, indent=2))

    # Passed the gate: persist the finding + its source bundle, seed a belief.
    _write_finding(fid, draft, sources)
    bid = world_model.add_belief(
        claim=draft["claim"],
        confidence=draft["confidence"],
        status="unverified",
        source_finding=fid,
    )
    return (f"FINDING {fid} emitted (gate passed) -> belief {bid} "
            f"@ {draft['confidence']:.2f} unverified. Prediction resolves "
            f"{draft['prediction'].get('resolves')}.")


def _next_finding_id():
    existing = list(FINDINGS_DIR.glob("F-*")) if FINDINGS_DIR.exists() else []
    n = 1 + max((int(p.stem.split("-")[1]) for p in existing
                 if p.stem.split("-")[1].isdigit()), default=0)
    return f"F-{n:03d}"


def _write_finding(fid, finding, sources):
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (FINDINGS_DIR / f"{fid}.json").write_text(json.dumps(finding, indent=2) + "\n")
    # Source bundle backing the finding (the cited evidence, archived).
    bundle_dir = RUNS_DIR / fid
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(sources):
        (bundle_dir / f"source-{i+1}.txt").write_text(
            f"{s['url']}\n\n{s['text']}")


def _maybe_escalate(urls):
    """If any fetched source has persistently failed, surface the recommended
    self-heal action. Does NOT open a PR itself (that's the caller's choice via
    propose_change) — only reports, honoring the report-first posture."""
    for url in urls:
        esc, action, reason = probes.should_escalate_source(url)
        if esc:
            print(f"  [self-heal] {url}: {action} — {reason}")


def auto_score_signals(signals, dry_run=False):
    """LLM-score any unscored signals using prompts/score_signal.md (5 dimensions).
    Returns the updated list. Best-effort: silently skips on any model/parse failure
    so a scoring hiccup never blocks the investigation step."""
    if not SCORE_PROMPT_PATH.exists():
        return signals
    prompt_template = SCORE_PROMPT_PATH.read_text()

    import os
    models = [m for m in observe.resolve_models()
              if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])]
    if not models:
        print("  (no model available for scoring — skipping)")
        return signals
    model = models[0]  # cheapest available (Sonnet by default)

    updated = []
    for sig in signals:
        if not all(v == 0 for v in sig.get("scores", {}).values()):
            updated.append(sig)
            continue
        if dry_run:
            print(f"  [dry-run] would score: {sig['title'][:60]}")
            updated.append(sig)
            continue
        prompt = (f"{prompt_template}\n\nSignal to score:\n"
                  f"Title: {sig['title']}\n"
                  f"Summary: {sig.get('summary', '')}")
        try:
            reply = observe.generate_observations(f"{SYSTEM}\n\n{prompt}", model)
            scored = _extract_json(reply)
            if (scored
                    and isinstance(scored.get("durability"), int)
                    and isinstance(scored.get("leverage"), int)):
                sig = dict(sig)
                sig["scores"] = {
                    "durability":    int(scored.get("durability", 0)),
                    "leverage":      int(scored.get("leverage", 0)),
                    "alignment":     int(scored.get("alignment", 0)),
                    "accessibility": int(scored.get("accessibility", 0)),
                    "novelty":       int(scored.get("novelty", 0)),
                }
                print(f"  scored: {sig['title'][:55]} "
                      f"(d={sig['scores']['durability']} "
                      f"l={sig['scores']['leverage']} "
                      f"a={sig['scores']['alignment']})")
        except Exception as exc:
            print(f"  ! scoring failed for '{sig['title'][:40]}': {exc}")
        updated.append(sig)
    return updated


def main():
    dry_run = "--dry-run" in sys.argv
    signal = _load_opportunity()
    if signal is None:
        print("No signal with a source link to investigate. "
              "Run fetch_signals.py first.")
        return
    mode = "firecrawl search" if firecrawl_client.is_enabled() else "stdlib only"
    print(f"\n🔬 SEAS Stage 1 — investigating: {signal['title'][:70]}")
    print(f"   related-source mode: {mode}  | dry-run: {dry_run}\n")
    result = investigate(signal, dry_run=dry_run)
    print("\n" + result + "\n")


if __name__ == "__main__":
    main()
