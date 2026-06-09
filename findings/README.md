# Findings log

Durable, gate-passed findings from the SEAS V3 pipeline. Each is grounded in
cross-source convergence with verbatim quotes (verified as real substrings of the
fetched sources), carries a dated falsifiable prediction, and seeds a belief in
[`data/world_model.json`](../data/world_model.json) at 0.30 confidence that can
only move via evidence or a scored prediction.

See [docs/SEAS_PIPELINE.md](../docs/SEAS_PIPELINE.md) for how the gate works, and
[`data/probes.json`](../data/probes.json) for the honest dead-ends (including a run
where the gate **caught a fabricated quote**).

| ID | Date | Belief | Conf. | Prediction resolves | Claim (short) |
|---|---|---|---|---|---|
| [F-002](F-002.json) | 2026-06-08 | WM-001 | 0.30 unverified | 2026-07-15 | Two independent studies: agentic pathology systems beat baselines on VQA / MC diagnosis |
| [F-003](F-003.json) | 2026-06-08 | WM-002 | 0.30 unverified | 2026-12-31 | On two long-horizon agent benchmarks, frontier agents finish <30% of end-to-end tasks |
| [F-004](F-004.json) | 2026-06-08 | WM-003 | 0.30 unverified | 2027-06-30 | LLM personalization is substantially worse on realistic vs synthetic data |

Each finding's source bundle (the exact fetched pages backing its quotes) is in
[`runs/F-NNN/`](../runs/).

## F-001 is a deliberate negative example

[`F-001-cognitive-operators.md`](F-001-cognitive-operators.md) predates the gate.
It's a V1 prose "finding" that asserts a confidence with no cited evidence, no
dated prediction, and no refutation condition — it would **fail** today's
emission gate on all three. It's kept, unmodified, as the contrast that defines
what changed between V1 (assert) and V3 (earn).
