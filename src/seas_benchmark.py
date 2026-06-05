"""
SEAS model benchmark — A/B two models on the SAME synthesis task, scored
objectively by the emission gate.

Why this exists: choosing ARGO_MODEL (Opus 4.8 vs Sonnet 4.6 vs ...) shouldn't be
a vibe. The V3 discipline says measure, don't assert. We already have an OBJECTIVE
scorer: seas_schema.validate_finding (the emission gate). So we can quantify
synthesis quality per model instead of eyeballing one finding.

Fairness is the whole point: inputs are held constant. We fetch each signal's
topical source pool ONCE, then replay the identical sources to every model, so we
compare MODELS, not different tasks. (A model that got luckier sources would
otherwise look better.)

Metrics per model, over N signals:
  - findings / probes split (did it synthesize or honestly abstain?)
  - gate pass rate among its 'finding' verdicts (over-claiming shows up as the
    model saying 'finding' but failing the gate)
  - avg sources cited in passing findings (evidence richness)
  - quote-verifiability: are cited quotes real substrings of the fetched sources?
    (catches a model fabricating evidence)
  - latency + a rough token proxy (output length) per call

This is reusable for every future model decision (incl. H2.4 tool discovery).
Standard-library only; reuses seas_finding's gathering/prompt/parse + the gate.

Run:  python src/seas_benchmark.py --models claude-opus-4-8,claude-sonnet-4-6 [--n 5]
"""

import json
import sys
import time
from pathlib import Path

import argo_observe as observe
import seas_finding as sf
import seas_schema

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = ROOT / "data" / "signals.json"
RESULTS_PATH = ROOT / "data" / "benchmark_results.json"

# Public list prices, USD per 1M tokens (input, output), for cost-benefit ranking.
# Approximate / update as prices change — we compare RELATIVE cost, not bill exactly.
# A model with no entry is priced None (cost columns blank, quality still scored).
PRICING = {
    "claude-opus-4-8":   (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "gpt-5":             (1.25, 10.0),
    "gpt-5-pro":         (15.0, 120.0),
    "gpt-5-mini":        (0.25, 2.0),
    "gpt-5-nano":        (0.05, 0.40),
    "gpt-4o":            (2.5, 10.0),
    "gpt-4.1":           (2.0, 8.0),
}

# Tokens ~ chars/4 (standard rough approximation; fine for relative ranking).
CHARS_PER_TOKEN = 4


def _est_cost(model, in_chars, out_chars):
    """Estimate one call's USD cost from input+output sizes. None if unpriced."""
    price = PRICING.get(model)
    if not price:
        return None
    in_tok = in_chars / CHARS_PER_TOKEN
    out_tok = out_chars / CHARS_PER_TOKEN
    return (in_tok * price[0] + out_tok * price[1]) / 1_000_000


def _signals(n):
    sigs = json.loads(SIGNALS_PATH.read_text()) if SIGNALS_PATH.exists() else []
    return [s for s in sigs if s.get("link")][:n]


def _judge_with_model(job, model):
    """One model call. Returns (judgment, latency_s, out_len, cost_usd)."""
    full_input = f"{sf.SYSTEM}\n\n{job}"
    t0 = time.time()
    try:
        reply = observe.generate_observations(full_input, model)
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}, time.time() - t0, 0, None
    cost = _est_cost(model, len(full_input), len(reply or ""))
    return sf._extract_json(reply), time.time() - t0, len(reply or ""), cost


def _quote_verifiable(judgment, sources):
    """Fraction of cited source-quotes that are REAL substrings of the fetched
    source text. A model fabricating evidence scores low here."""
    text_by_url = {s["url"]: s["text"] for s in sources}
    quotes = [(e.get("url"), e.get("quote", "")) for e in judgment.get("evidence", [])
              if isinstance(e, dict) and e.get("quote")]
    if not quotes:
        return None
    hits = 0
    for url, q in quotes:
        body = text_by_url.get(url, "") or " ".join(text_by_url.values())
        # lenient: a 20-char window of the quote appears in the source text
        probe = q.strip()[:40]
        if probe and probe.lower() in body.lower():
            hits += 1
    return hits / len(quotes)


def _score_judgment(judgment, sources):
    """Turn one model's judgment into objective metrics. Returns a dict row."""
    if judgment is None:
        return {"outcome": "unparseable"}
    if "_error" in judgment:
        return {"outcome": "error", "error": judgment["_error"]}
    verdict = judgment.get("verdict")
    if verdict != "finding":
        return {"outcome": "probe", "verdict": verdict}

    # It claimed a finding — does it pass the gate?
    pred = judgment.get("prediction") or {}
    if pred and not pred.get("resolves"):
        pred = {**pred, "resolves": "2026-12-31"}  # neutral default for scoring
    draft = seas_schema.new_finding(
        finding_id="BENCH", claim=judgment.get("claim", ""), method="synthesis",
        evidence=judgment.get("evidence", []), prediction=pred,
        refutation_condition=judgment.get("refutation_condition", ""),
    )
    # Score through the FULL gate including quote-fidelity, so a model that
    # fabricates quotes is marked an overclaim, not a clean finding.
    passed, problems = seas_schema.validate_finding(draft, sources=sources)
    return {
        "outcome": "finding" if passed else "overclaim",
        "gate_passed": passed,
        "n_sources_cited": len([e for e in judgment.get("evidence", [])
                                if isinstance(e, dict)]),
        "quote_verifiable": _quote_verifiable(judgment, sources),
        "gate_problems": problems if not passed else [],
    }


def _aggregate(rows):
    """Roll per-signal rows into one model's summary."""
    total = len(rows)
    findings = [r for r in rows if r.get("outcome") == "finding"]
    overclaims = [r for r in rows if r.get("outcome") == "overclaim"]
    probes = [r for r in rows if r.get("outcome") == "probe"]
    errors = [r for r in rows if r.get("outcome") in ("error", "unparseable")]
    claimed = findings + overclaims  # times it said "finding"
    gate_pass_rate = (len(findings) / len(claimed)) if claimed else None
    srcs = [r["n_sources_cited"] for r in findings if "n_sources_cited" in r]
    qv = [r["quote_verifiable"] for r in findings
          if r.get("quote_verifiable") is not None]
    return {
        "trials": total,
        "findings": len(findings),
        "overclaims": len(overclaims),
        "probes": len(probes),
        "errors": len(errors),
        "gate_pass_rate": gate_pass_rate,
        "avg_sources_cited": (sum(srcs) / len(srcs)) if srcs else None,
        "avg_quote_verifiable": (sum(qv) / len(qv)) if qv else None,
    }


def run(models, n):
    signals = _signals(n)
    if not signals:
        print("No signals with links. Run fetch_signals.py first.")
        return

    print(f"\n📊 SEAS model benchmark — {len(models)} models × {len(signals)} "
          f"signals (inputs held constant)\n")

    per_model = {m: {"rows": [], "latency": [], "out_len": [], "cost": []}
                 for m in models}

    for i, sig in enumerate(signals, 1):
        # Fetch the topical source pool ONCE — identical inputs for every model.
        sources = sf._gather_sources(sig, dry_run=False)
        print(f"[{i}/{len(signals)}] {sig['title'][:55]} "
              f"({len(sources)} sources)")
        if len(sources) < sf.MIN_SOURCES:
            print("    too few sources; skipping (counts as no-trial)")
            continue
        sources_block = "\n\n".join(
            f"SOURCE {j+1} ({s['url']}):\n{s['text']}"
            for j, s in enumerate(sources))
        job = (f"{sf.INSTRUCTIONS}\n\n## OPPORTUNITY\n{sig['title']}\n"
               f"{sig.get('summary','')}\n\n## SOURCES\n{sources_block}")

        for m in models:
            judgment, lat, olen, cost = _judge_with_model(job, m)
            row = _score_judgment(judgment, sources)
            per_model[m]["rows"].append(row)
            per_model[m]["latency"].append(lat)
            per_model[m]["out_len"].append(olen)
            if cost is not None:
                per_model[m]["cost"].append(cost)
            costs = f", ${cost*100:.2f}/100" if cost is not None else ""
            print(f"    {m:22s} -> {row.get('outcome'):12s} ({lat:.1f}s{costs})")

    # Summary table.
    print("\n" + "=" * 86)
    print(f"{'model':22s} {'find':>4s} {'over':>4s} {'probe':>5s} "
          f"{'pass%':>5s} {'qv%':>4s} {'s':>5s} {'$/call':>8s} {'$/finding':>10s}")
    print("-" * 86)
    summary = {}
    for m in models:
        agg = _aggregate(per_model[m]["rows"])
        lat = per_model[m]["latency"]
        cost = per_model[m]["cost"]
        agg["avg_latency_s"] = (sum(lat) / len(lat)) if lat else None
        agg["avg_cost_usd"] = (sum(cost) / len(cost)) if cost else None
        agg["total_cost_usd"] = sum(cost) if cost else None
        # The bottom line: cost per CLEAN finding (a probe yields no finding, so a
        # model that probes everything has infinite $/finding — that's honest).
        agg["cost_per_finding"] = (
            (sum(cost) / agg["findings"]) if (cost and agg["findings"]) else None)
        summary[m] = agg
        pr = f"{agg['gate_pass_rate']*100:.0f}" if agg["gate_pass_rate"] is not None else "-"
        qv = f"{agg['avg_quote_verifiable']*100:.0f}" if agg["avg_quote_verifiable"] is not None else "-"
        sl = f"{agg['avg_latency_s']:.1f}" if agg["avg_latency_s"] is not None else "-"
        cc = f"${agg['avg_cost_usd']*1000:.2f}/k" if agg["avg_cost_usd"] is not None else "-"
        cpf = (f"${agg['cost_per_finding']*1000:.2f}/k" if agg["cost_per_finding"] is not None
               else ("none" if agg["findings"] == 0 else "-"))
        print(f"{m:22s} {agg['findings']:>4d} {agg['overclaims']:>4d} "
              f"{agg['probes']:>5d} {pr:>5s} {qv:>4s} {sl:>5s} {cc:>8s} {cpf:>10s}")
    print("=" * 86)
    print("find=clean findings  over=claimed-but-failed-gate  pass%=findings/claims")
    print("qv%=quotes verifiable  s=latency  $/call & $/finding shown PER 1000 runs")
    print("(none under $/finding = produced 0 findings, so cost-per-finding undefined)\n")

    RESULTS_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Wrote {RESULTS_PATH.relative_to(ROOT)}")
    return summary


def main():
    models = ["claude-opus-4-8", "claude-sonnet-4-6"]
    n = 5
    for i, a in enumerate(sys.argv):
        if a == "--models" and i + 1 < len(sys.argv):
            models = [m.strip() for m in sys.argv[i + 1].split(",") if m.strip()]
        if a == "--n" and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    run(models, n)


if __name__ == "__main__":
    main()
