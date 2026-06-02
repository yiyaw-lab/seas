"""
Argo V1 — Weekly Bet + Energy Tracking.

Argo is a decision engine, not an AI agent. It places one Bet per week
(confidence x upside) and records how much energy Yiya actually has to build it.
See ARGO_ARCHITECTURE.md.

V1 scope (deliberately minimal — run manually for 3-5 weeks before adding more):
  - pick ONE Bet from a curated frontier pool
  - print it in the weekly format (the Bet only — no scored forecast shown)
  - ask Yiya for her actual Energy Score (1-10)
  - store {bet, energy_prediction, energy_actual, energy_delta, date}
    in data/argo_bets.json

Energy Prediction is INTERNAL: stored for later analysis but never displayed,
so it cannot anchor Yiya's reported energy.

NOT in V1: Telegram read-side, /another, /built, state machine, SEAS plumbing.
Standalone: imports nothing from seas.py / seas_demo.py and is not invoked by them.

Run with:  python src/argo.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BETS_PATH = ROOT / "data" / "argo_bets.json"

TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Curated frontier Bet pool.
# Hand-written for the manual run. The first Bet is the one named in
# ARGO_ARCHITECTURE.md. Argo picks one per week (oldest-unbet first, so it
# rotates rather than repeating). Add Bets here; do not over-engineer.
# ---------------------------------------------------------------------------

BET_POOL = [
    {
        "id": "B-001",
        "bet": "Agent Organization Lab",
        "confidence": "Medium",
        "potential_upside": "High",
        "why_now": "Subagents just made agent *organization* a tunable variable. "
                   "The frontier question shifted from 'which model?' to 'which "
                   "structure?' — and almost nobody is measuring it.",
        "build_this_week": "A tiny harness that runs ONE frontier signal through "
                           "3 structures (Single Agent, Researcher, "
                           "Researcher+Critic) and labels the thinking mode each "
                           "produces.",
        "artifact": "Public repo + short writeup: 'Agent Structure -> Thinking "
                    "Mode' with a results table anyone can re-run.",
        "reason": "Could reveal organizational laws of intelligence.",
        "energy_prediction": 8,
    },
    {
        "id": "B-002",
        "bet": "Cognitive Operators Map",
        "confidence": "Low",
        "potential_upside": "High",
        "why_now": "F-001 hints that agent structure changes *how* an agent "
                   "thinks, but it's only been tested on 2 signals. The map is "
                   "wide open and nobody has named it.",
        "build_this_week": "Run 3 fresh frontier signals through the Researcher "
                           "structure and check whether it keeps producing Theory "
                           "Thinking. Publish signal #3, #4, #5.",
        "artifact": "An updated cognitive-operations table + a one-page claim: "
                    "'structure is a cognitive operator,' with evidence.",
        "reason": "Turns a tentative finding into a defensible frontier theory.",
        "energy_prediction": 7,
    },
    {
        "id": "B-003",
        "bet": "Argo Energy Loop (dogfood)",
        "confidence": "High",
        "potential_upside": "Medium",
        "why_now": "Argo's most novel idea — optimizing for *energy* — is "
                   "untested. The fastest way to learn if energy is the right "
                   "signal is to run it on yourself.",
        "build_this_week": "Use this very tool every Monday for the week; log "
                           "predicted vs actual energy and whether you built.",
        "artifact": "A short note: 'Does energy predict shipping?' with your "
                    "first weeks of data.",
        "reason": "Validates Argo's core optimization target before building more.",
        "energy_prediction": 6,
    },
]


def load_bets_log():
    if BETS_PATH.exists():
        return json.loads(BETS_PATH.read_text())
    return []


def save_bets_log(log):
    BETS_PATH.write_text(json.dumps(log, indent=2) + "\n")


def select_bet(log):
    """Pick the first pool Bet that hasn't been placed yet; else rotate to the
    least-recently-placed one. Keeps the manual run from repeating."""
    placed_ids = [entry["id"] for entry in log]

    for bet in BET_POOL:
        if bet["id"] not in placed_ids:
            return bet

    # All have been placed at least once: pick the one placed longest ago.
    last_seen = {entry["id"]: i for i, entry in enumerate(log)}
    return min(BET_POOL, key=lambda b: last_seen.get(b["id"], -1))


def format_bet(bet):
    return f"""🌊 Argo — This Week's Bet ({TODAY})

Bet:
{bet['bet']}

Confidence:
{bet['confidence']}

Potential Upside:
{bet['potential_upside']}

Why Now:
{bet['why_now']}

Build This Week:
{bet['build_this_week']}

Artifact:
{bet['artifact']}
"""


def ask_energy():
    """Prompt for the actual Energy Score. Returns int 1-10, or None if skipped
    (non-interactive / blank input)."""
    prompt = "\nHow much do you want to build this? (1-10) "
    try:
        raw = input(prompt).strip()
    except EOFError:
        # Non-interactive context (e.g. CI): skip energy capture gracefully.
        print("(no input available — energy not recorded)")
        return None

    if raw == "":
        return None

    try:
        value = int(raw)
    except ValueError:
        print("Not a number — energy not recorded.")
        return None

    if not 1 <= value <= 10:
        print("Out of range (1-10) — energy not recorded.")
        return None

    return value


def main():
    log = load_bets_log()
    bet = select_bet(log)

    print()
    print(format_bet(bet))

    energy_actual = ask_energy()

    # energy_prediction and energy_delta are internal only — stored for later
    # analysis, never shown to the user, to avoid anchoring her actual energy.
    energy_delta = (
        energy_actual - bet["energy_prediction"]
        if energy_actual is not None
        else None
    )

    entry = {
        "id": bet["id"],
        "date": TODAY,
        "bet": bet["bet"],
        "confidence": bet["confidence"],
        "potential_upside": bet["potential_upside"],
        "energy_prediction": bet["energy_prediction"],  # internal
        "energy_actual": energy_actual,
        "energy_delta": energy_delta,  # internal
    }
    log.append(entry)
    save_bets_log(log)

    print(f"\nRecorded bet {bet['id']} for {TODAY}.")
    print("\n✅ Argo weekly bet complete.\n")


if __name__ == "__main__":
    main()
