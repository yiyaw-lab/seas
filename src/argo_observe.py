"""
Argo V2 — Phase A: Observation generator (sidecar).

Generates OBSERVATIONS only — the bottom of the V2 funnel (see ARGO_V2.md,
ARGO_V2_MIGRATION.md). An Observation is a noticing: a true, specific statement
about what the field is paying attention to, and what it is walking past.

This script does NOT fake insight. There is no LLM client wired into this repo
yet (no `anthropic` SDK, no API key), so instead of hardcoding observations it
does the real work it *can* do offline:

  1. loads 2-3 signals + F-001 as context,
  2. assembles a complete, reusable observation-generation job (the "everyone /
     but" prompt + the real inputs),
  3. writes that job to argo/observations/observation_job.md,
  4. writes argo/observations/latest.md (the job + a results placeholder for the
     ~7 observations to be filled in when the job is run by an LLM / by hand),
  5. prints to the terminal.

Phase A scope only. Does NOT: select a bet, write data/argo_bets.json, touch
Argo V1 (argo.py), or wire Telegram. Standalone — imports nothing from argo.py.

Run with:  python src/argo_observe.py
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = ROOT / "data" / "signals.json"
FINDING_PATH = ROOT / "findings" / "F-001-cognitive-operators.md"
OUT_DIR = ROOT / "argo" / "observations"

TODAY = datetime.now().strftime("%Y-%m-%d")

# How many signals to feed in (Phase A: lean input, 2-3) and how many
# observations the job should ask for.
NUM_SIGNALS = 3
NUM_OBSERVATIONS = 7


# ---------------------------------------------------------------------------
# Instructions — the generation method, not the output.
# This is the "everyone / but" move from ARGO_V2.md. It tells the model HOW to
# notice; it does NOT contain any pre-written observations.
# ---------------------------------------------------------------------------

INSTRUCTIONS = f"""You are Argo, a frontier scout. Your job in this task is to
NOTICE — to generate {NUM_OBSERVATIONS} original Observations about the frontier
signals below.

An Observation is:
- a true, specific statement about what the field is paying ATTENTION to —
  and, just as importantly, what it is walking past;
- descriptive, not prescriptive (it does NOT recommend a project);
- often slightly obvious-in-hindsight: "...huh, yeah, that IS true."

Use the "everyone / but" move:
  "Everyone is focused on X. But the thing that may actually matter is Y."
X comes from the signals. Y is the leap — that is where originality lives.

Also try:
- crossing two signals against each other (what pattern do they share?);
- inverting the consensus (what's true if the opposite is?);
- naming the blind spot next to where everyone is looking.

Rules:
- Generate {NUM_OBSERVATIONS} DISTINCT observations. Quantity first; most will be
  mediocre, that is expected.
- Do NOT propose projects, bets, or actions. Observations only.
- Each observation: 1-3 short sentences. No headings.
- Aim for at least one that the reader would NOT have thought of themselves.

Output format: a numbered list, 1 to {NUM_OBSERVATIONS}, nothing else.
"""


def load_signals():
    signals = json.loads(SIGNALS_PATH.read_text())
    return signals[:NUM_SIGNALS]


def format_signals(signals):
    lines = []
    for i, s in enumerate(signals, start=1):
        lines.append(
            f"{i}. {s['title']}\n"
            f"   Source: {s.get('source', '')}\n"
            f"   Category: {s.get('category', '')}\n"
            f"   Summary: {s.get('summary', '')}"
        )
    return "\n\n".join(lines)


def build_job(signals_block, signal_count, finding_text):
    """Assemble the full, self-contained observation-generation job."""
    return f"""# Argo Observation Job — {TODAY}

Phase A (ARGO_V2_MIGRATION.md): generate observations only. No bet, no selection.

---

## Instructions

{INSTRUCTIONS}
---

## Frontier signals ({signal_count} of them)

{signals_block}

---

## Context — Finding F-001 (cross-signal pattern, raw material for noticing)

{finding_text}
"""


def build_latest(job):
    """latest.md = the job plus a results placeholder where the ~7 observations
    get filled in when the job is actually run."""
    return f"""{job}
---

## Observations

<!--
Run the job above through an LLM (or answer it yourself) and paste the
{NUM_OBSERVATIONS} observations here. Phase A success = at least one observation
the reader would not have thought of themselves (the Surprise Test).
Do NOT select a bet here — observations only.
-->

_(not yet generated — run the job above)_
"""


def main():
    if not SIGNALS_PATH.exists():
        raise SystemExit(f"Missing input: {SIGNALS_PATH.relative_to(ROOT)}")
    if not FINDING_PATH.exists():
        raise SystemExit(f"Missing input: {FINDING_PATH.relative_to(ROOT)}")

    signals = load_signals()
    signals_block = format_signals(signals)
    finding_text = FINDING_PATH.read_text().strip()

    job = build_job(signals_block, len(signals), finding_text)
    latest = build_latest(job)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    job_path = OUT_DIR / "observation_job.md"
    latest_path = OUT_DIR / "latest.md"
    job_path.write_text(job)
    latest_path.write_text(latest)

    print("\n🧭 Argo — Observe (Phase A)\n")
    print(f"Loaded {len(signals)} signals + F-001 as context:")
    for s in signals:
        print(f"  • {s['title']}")
    print()
    print("No LLM client is wired in yet, so Argo assembled a reusable")
    print("observation-generation job (it did NOT fabricate observations).")
    print()
    print("Generated:")
    print(f"  - {job_path.relative_to(ROOT)}   (reusable job)")
    print(f"  - {latest_path.relative_to(ROOT)}        (job + results placeholder)")
    print()
    print(f"Next: run the job through an LLM to produce {NUM_OBSERVATIONS} "
          "observations,")
    print(f"      then paste them into {latest_path.relative_to(ROOT)}.")
    print("\n✅ Observation job ready.\n")


if __name__ == "__main__":
    main()
