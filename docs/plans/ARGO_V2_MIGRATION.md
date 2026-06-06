# ARGO V2 Migration — First Implementation Path

> Optimize for **learning**, not architecture. The whole point of this plan is
> to find out, as cheaply as possible, whether Argo can generate a genuine
> insight — not to build the insight engine.

Date: 2026-06-02
Status: Plan only. No code, no file changes. V1 stays in production untouched.

Companion to: [ARGO_V2.md](../architecture/ARGO_V2.md) (approved). V1 reference:
[ARGO_ARCHITECTURE.md](../architecture/ARGO_ARCHITECTURE.md), [src/argo.py](../../src/argo.py).

## Strategy in one line

V1 already proves delivery + weekly bets + energy. **V2 only has to prove one
thing: insight generation.** So the migration builds *nothing* that V1 already
covers, and *only* the thinnest path to a generated observation worth reading.

---

## 1. The Smallest Implementation of Insight Generation

A script that takes **2–3 frontier signals** and produces **~7 Observations**
using the "everyone / but" move — then stops. That's it.

```
input:  a handful of signals (text)
prompt: "Everyone is focused on X. But the thing that may actually matter is Y."
        applied across and between the signals.
output: ~7 short observations, printed. Yiya reads them and stars the good ones.
```

No selection logic, no insight object, no bet, no storage, no delivery. The
generator + a human judge **is** the minimum. If 1 of 7 makes Yiya think "huh,
I wouldn't have said that" — V2 has signal. Everything else is deferred.

The generation step is necessarily an LLM reasoning call (you cannot retrieve
your way to originality). The *smallness* is in refusing to build anything
around it.

## 2. What Argo Should Consume

Candidates assessed against "does it help produce a surprising observation
*right now*":

| Source | Necessary for V1-of-V2? | Why |
|---|---|---|
| **`data/signals.json`** | **Yes** | The raw "what changed." Observations are *about* attention to signals. This is the irreducible input. |
| **`findings/`** | **Yes (light)** | F-001 is the strongest raw material we have — a cross-signal pattern is *already* halfway to an insight. One finding is enough to start. |
| `data/opportunities.json` | No | It's just re-scored signals (per the audit). Adds scoring noise, no new insight material. Skip. |
| `theories/` | No (not yet) | Doesn't exist as a folder yet; the one theory lives in `results/`. A finished theory is *settled* — low insight yield. Skip for now. |
| frontier briefs | No | Gen-2 artifact, archived-track. Not needed to test generation. Skip. |

**Verdict: signals.json + one finding.** Two sources. Resist adding more — more
inputs make it *harder* to tell whether the insight came from Argo's thinking or
from a rich input doing the work. Lean input = cleaner evidence.

## 3. The First V2 Object

**Observation.** Not Insight, not Theme.

- **Observation** is the cheapest unit to generate and the easiest for a human
  to judge ("is this true and non-obvious?"). It's the bottom of the funnel
  where originality is actually searched.
- **Insight** is a *promotion* of a good Observation — premature to model before
  we know Argo can even produce good Observations.
- **Theme** is an *accumulation* across weeks — meaningless until there are weeks
  of insights to accumulate.

Build the bottom of the funnel first. If Argo can't generate a surprising
Observation, nothing above it matters. Observation is the load-bearing
experiment.

## 4. What Stays Manual

Everything except generation. Deliberately.

- **Selection** — Yiya reads the ~7 observations and picks/stars the good one(s).
  Human judgment is the gold-standard evaluator; automating it now would hide the
  very signal we're trying to measure.
- **Promotion** Observation → Insight → Bet — manual, by Yiya, if at all.
- **Delivery** — none in the experiment. Run it in the terminal. (V1 owns
  Telegram; V2 doesn't touch it.)
- **Energy / artifact tracking** — V1's job, untouched.
- **Signal curation** — Yiya still chooses which signals go in.

The manual parts are not tech debt; they are the **measurement instrument.**

## 5. What Gets Generated

Exactly one thing: **the Observations.** Argo's only new capability in this
phase is "look at signals and notice something non-obvious about the field's
attention." That single generative act is the entire hypothesis under test.

## 6. The First V2 Command

Exactly one:

```
/observe
```

Takes the current signals (+ a finding), generates ~7 Observations, prints them.
Nothing else. No `/insight`, no `/bet`, no `/promote` — those are premature until
`/observe` produces things worth promoting.

(In practice this is `python src/argo_observe.py` — a *separate* script, so V1's
`argo.py` is never touched.)

## 7. How V1 and V2 Coexist

They run as **two independent scripts that share read-only inputs and never
import each other.**

```
                ┌─────────────────────────────┐
   signals.json │  Argo V1  (argo.py)          │  → weekly Bet + energy   [PRODUCTION]
   findings/  ──┤                              │
                │  Argo V2  (argo_observe.py)  │  → ~7 Observations       [EXPERIMENT]
                └─────────────────────────────┘
```

- **No coupling.** V2 is a new file (`argo_observe.py`); V1 (`argo.py`) is not
  modified, imported, or invoked by it. Either can break without touching the
  other.
- **Shared inputs, read-only.** Both read `signals.json` / `findings/`. Neither
  writes the other's data. (V1 owns `argo_bets.json`; V2, if it stores anything,
  uses its own file.)
- **Interaction is manual and one-directional, later.** Once `/observe` is
  producing good Observations, a *human* may hand a chosen Observation's bet into
  V1's pool. That's the only bridge — and it's a copy-paste, not a code path.
- **Separate workflows.** V1 keeps the Friday Telegram job. V2 has no schedule;
  it's run by hand during the experiment.

Coexistence rule: **V2 may read V1's world but must never write to it or block
it.** Production stays green by construction.

## 8. The Smallest Test That Proves Generation (not Retrieval)

**The Surprise Test.**

> Run `/observe` on signals Yiya knows well. For each Observation she marks:
> *"Would I have thought of this myself?"* — Yes / No.
>
> **Pass:** across a few runs, Argo reliably produces ≥1 "No" (surprising-but-true)
> Observation per run.

Why this isolates generation from retrieval:
- Retrieval can only return things already in its inputs — Yiya, who knows the
  inputs, would answer "Yes, obvious." A "No" means Argo *added* something.
- The judge is the person with the most context, so the bar is honest.
- It measures the only thing that matters: did thinking happen?

Sharper variant (optional): a **blind test** — mix Argo's Observations with a
few of Yiya's own, unlabeled. If she can't reliably tell which are hers, and
rates some of Argo's higher, that's strong evidence of genuine generation.

## 9. What Would Falsify the V2 Hypothesis

V2's hypothesis: *Argo can generate original insight, not just retrieve bets.*

It is falsified if, after a fair run of `/observe`:

- **Everything is obvious.** Observations are all things Yiya would have said
  herself (all "Yes" on the Surprise Test). → Argo is paraphrasing, not noticing.
- **Everything is restatement.** Observations just re-describe the signals with
  no "everyone / but" turn. → No reframe = no insight, only summary.
- **Surprise doesn't survive scrutiny.** The "No" ones are surprising because
  they're *wrong*, not because they're non-obvious-but-true. → Novelty without
  truth is noise.
- **It can't beat the human.** In the blind test, Yiya's own observations are
  consistently better. → Argo isn't additive.

Crucial discipline: **decide the falsification bar before running.** Pre-commit
to "if fewer than X surprising-and-true observations across Y runs, V2 is not
working *yet*." (This mirrors SEAS's own `FALSIFICATION_CRITERIA` habit.) A
hypothesis you can't fail is marketing, not an experiment.

## 10. Staged Migration

### Phase A — Generate (prove the act of noticing)
- New file `argo_observe.py`. Reads `signals.json` + one finding. `/observe`
  generates ~7 Observations and prints them. No storage, no selection, no bet.
- **Run the Surprise Test by hand** for 2–3 sessions.
- **Gate:** does Argo reliably produce ≥1 surprising-but-true Observation?
  - **No →** stop. Iterate the generation prompt, or accept V2 is falsified for
    now. Do *not* proceed. (V1 keeps running regardless.)
  - **Yes →** proceed.

### Phase B — Capture + select (prove insights compound)
- Add lightweight storage: an Observation/Insight log (V2's own file). Yiya
  marks which Observations were surprising and promotes the best to *Insights*.
- Now there's a record of Argo's thinking over time.
- **Gate:** after several weeks, do promoted Insights start forming a recurring
  **Theme** — i.e., is a point of view emerging (ARGO_V2 Q7)?

### Phase C — Bridge to V1 (insight → action, only once earned)
- The chosen Insight's Bet is fed (manually first) into V1's weekly flow — the
  insight now *precedes* the bet, closing the V1↔V2 loop.
- Only after this is working by hand, consider automating the Observation →
  Insight → Bet handoff. Insight-led delivery becomes the new front of the
  Friday message.
- **Only here** does it become reasonable to discuss retiring V1's static
  `BET_POOL` (ARGO_V2 Q9) — and only because something better now feeds it.

Each phase has a gate that can **stop the migration without harming V1.** If
Phase A fails, nothing was lost but a small script and some good evidence.

---

## Guardrails (the spirit of this plan)

- **V1 is production; V2 is an experiment.** Never let the experiment touch the
  production path.
- **One new capability at a time:** generate (A) → capture/select (B) → bridge (C).
- **Prefer evidence over elegance.** The manual steps are the instrument; don't
  automate away your own measurement.
- **The bar is surprise-and-truth**, judged by the person with the most context.
- **Pre-commit to falsification.** Know what "it didn't work" looks like before
  you run it.
- Don't build the insight engine. Build the smallest thing that tells you whether
  one is possible.
