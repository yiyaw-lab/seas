# SEAS V3: how a finding is earned

The common objection to any "AI research" system is that it's a language model
laundering its own guesses back out as conclusions. SEAS is built specifically to
*not* be that. The mechanism is an **emission gate**: the model proposes a
finding, and a deterministic gate disposes of it. The model's say-so is never
enough.

This doc walks the real pipeline using the findings and probes committed in this
repo — not a toy example.

```
signal → score → rank → topical sources → synthesis → EMISSION GATE → finding | probe
                                                            │
                                              belief (world model) + dated prediction
```

## The pipeline (src/seas.py)

1. **fetch_signals** — pull the signal pool from curated frontier feeds (stdlib RSS).
2. **auto_score** — LLM-score unscored signals on 5 dimensions
   (durability / leverage / alignment / accessibility / novelty).
3. **opportunities.build** — rank by weighted score; surface qualifying signals.
4. **seas_finding.investigate** — gather a *topical* source pool (the signal's own
   source + Firecrawl-searched related sources on the same topic), ask the model
   to propose a finding grounded only in those sources, then run the gate.

## The emission gate (src/seas_schema.py `validate_finding`)

Five checks, all must pass. The first four are structural; the fifth is the one
that matters most:

1. **Evidence** — ≥2 items, at least one an external source/artifact.
2. **Prediction** — a dated (`YYYY-MM-DD`) and checkable forecast the claim implies.
3. **Claim / method / refutation** — non-empty claim, a known method, and an
   explicit refutation condition (what would make this false).
4. **Quote fidelity** — **every cited quote must be a real substring of its fetched
   source.** A model that paraphrases or fabricates a quote fails here. (An earlier
   benchmark found a large fraction of model-generated "quotes" were not verbatim;
   this check exists to catch exactly that.)

Pass → the finding is written to `findings/` with its source bundle in
`runs/F-NNN/`, and it seeds a belief. Fail → an honest **probe** is recorded
instead, so SEAS remembers the dead end and doesn't re-investigate it.

## Worked example: a finding that was earned

[`findings/F-002.json`](../findings/F-002.json) — emitted on the first pipeline run:

> **Claim:** Across two independent 2025–2026 studies, proposed agentic pathology
> systems report higher accuracy than the compared multimodal/pathology baselines
> on complex tasks such as visual question answering and multiple-choice diagnosis.

- **Evidence:** two *different* arXiv papers (`2606.07549`, `2508.02258`), each
  with a verbatim quote. Both quotes are confirmed real substrings of the fetched
  pages in [`runs/F-002/`](../runs/F-002/).
- **Prediction (resolves 2026-07-15):** each paper's results will show ≥1 table
  where the proposed method beats all listed baselines on VQA or MC diagnosis.
- **Refutation:** if the detailed results don't show that advantage, the claim is
  false.
- **Seeded belief:** `WM-001 @ 0.30 confidence, status unverified`.

Two more findings were earned the same way on later signals:
[`F-003`](../findings/F-003.json) (long-horizon agent benchmarks — frontier agents
complete <30% of end-to-end tasks) and [`F-004`](../findings/F-004.json) (LLM
personalization is substantially worse on realistic vs synthetic data).

## The gate doing its job (the credibility)

A repo that only shows wins is suspect. These probes are committed too
([`data/probes.json`](../data/probes.json)):

- **PR-001 — fabrication caught (inconclusive).** On the MemPalace signal the model
  *claimed a finding*, but one cited quote was **not found in its source**. The
  gate rejected it: `"evidence quote not found in its source (possible
  fabrication)"`. This is the anti-hallucination claim demonstrated on a real run,
  not asserted.
- **PR-002 — no convergence (premature).** Both URLs for the Sensitivity Analysis
  white paper pointed at the *same* newly-posted paper. With no independent
  corroboration, no cross-source claim can be defended, so SEAS abstains.

## The world model (src/world_model.py)

A finding seeds a belief at **0.30 confidence**. From there, confidence moves
**only** by evidence or by a scored prediction — there is no `set_confidence()`:

- `add_evidence` → ±0.05
- a resolved prediction → ±0.20; three correct predictions promote a belief toward
  theory (≥0.95), three wrong ones refute it (≤0.05).

So a belief gets stronger because a dated forecast it made came true, not because
the model felt more sure. That's the entire point.

## Contrast: the pre-gate artifact

[`findings/F-001-cognitive-operators.md`](../findings/F-001-cognitive-operators.md)
is kept on purpose as the **negative example**. It's a V1 prose "finding" that
asserts a confidence with no cited evidence, no dated prediction, and no refutation
condition. Run it through today's gate and it fails on all of those. The
difference between F-001 and F-002 *is* the difference between V1 and V3.

## Model benchmark (src/seas_benchmark.py)

Choosing the synthesis model is measured, not vibed: the same source pool is
replayed to each model and scored by the same gate. Results from
[`data/benchmark_results.json`](../data/benchmark_results.json) (4 scored signals,
inputs held constant):

| Model | Findings | Over-claims | Gate pass | Quote-verifiable | Avg latency | Cost / clean finding |
|---|---|---|---|---|---|---|
| claude-opus-4-8 | 1 | 0 | 100% | 100% | 6.6s | ~$0.48 |
| claude-sonnet-4-6 | 1 | 0 | 100% | 100% | 9.8s | ~$0.10 |

Two things to read off this. First, **neither model over-claimed** — zero cases of
"said finding, failed the gate" — and both honestly probed the same 3 signals where
convergence wasn't there. The gate discipline holds across models. Second, Sonnet
reaches the *same* finding quality at roughly **5× lower cost per clean finding**,
which is the measured justification for defaulting to Sonnet and escalating to Opus
only deliberately.

Reproduce: `PYTHONPATH=src python3 src/seas_benchmark.py --models claude-opus-4-8,claude-sonnet-4-6 --n 5`
(writes `data/benchmark_results.json`).
