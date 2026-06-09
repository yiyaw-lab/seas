# SEAS V3 Demo — One Pass of the Pipeline

> A walk of the V3 pipeline end-to-end: a signal becomes a finding only if it
> earns it. See [README](../README.md) for the full architecture.

```
Signal → score → rank → topical sources → synthesis → EMISSION GATE → finding | probe
```

The gate is the heart. It stops SEAS from laundering a signal's own description
back out as a "finding". A finding must cite real quotes from fetched sources;
a fabricated or unverifiable citation fails at the gate and becomes a probe instead.

---

## 1. Signal

**Extended thinking in Claude 3.7 Sonnet**
_source: Anthropic blog (ingested via fetch\_signals.py)_

Anthropic shipped an optional "extended thinking" mode: before answering,
the model produces a scratchpad of reasoning tokens the user never sees. The
result is measurably better on multi-step math and coding benchmarks.

SEAS score (5 dimensions): durability 4, leverage 5, alignment 5, accessibility 3,
novelty 4 — weighted 4.3, qualifies for investigation.

---

## 2. Investigation

`seas_finding.py` fetches the top-N topically related sources (via Firecrawl),
then asks the model to propose a finding grounded only in real quotes from those
pages. The proposed finding is a structured object:

```json
{
  "claim": "Inference-time compute is becoming a first-class design variable...",
  "evidence": [
    {
      "source_url": "https://www.anthropic.com/news/claude-3-7-sonnet",
      "quote": "extended thinking... allows Claude to think through difficult
               problems with greater focus and facility"
    }
  ],
  "prediction": {
    "text": "By Q4 2026 at least two more frontier labs will ship user-accessible
             thinking-token modes",
    "due_date": "2026-12-31",
    "falsification": "None of the top-5 labs (OpenAI, Google, Meta, Mistral, xAI)
                      ship a comparable feature by that date"
  },
  "confidence": 0.62
}
```

---

## 3. Emission gate

Before the finding is accepted, the gate checks:

| Check | Result |
|---|---|
| Cross-source convergence (≥2 independent sources corroborate the claim) | PASS |
| Cited quotes are real substrings of the fetched source pages | PASS |
| Prediction is dated and has an explicit falsification condition | PASS |
| Claim is not just a restatement of the original signal description | PASS |

All four pass → the finding is emitted to `findings/`.

If any check fails, SEAS emits a **probe** instead: an honest record of what it
looked at and why it couldn't conclude — so the same ground isn't re-investigated.

---

## 4. Finding

The accepted finding lands in `findings/` as a JSON + markdown pair. The claim
moves into `data/world_model.json` as a belief at confidence 0.62. When the
dated prediction resolves (true or false), `world_model.py` moves confidence up
or down by evidence — never by assertion.

---

## What makes V3 different from V1

| V1 (Signal → Opportunity → Experiment) | V3 (Signal → Gate → Finding) |
|---|---|
| Findings were assertions, not earned | Findings require cross-source evidence |
| No citation verification | Quoted text must be a real substring of the source |
| No prediction accountability | Every finding ships with a falsifiable dated forecast |
| Confidence set by the model | Confidence moves only via scored predictions |

V3 treats a finding the way a scientist treats a result: it has to be reproducible
(the source bundle is committed alongside it) and accountable (the prediction will
either confirm or weaken the belief).
