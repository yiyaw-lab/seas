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

import argo_paths

ROOT = Path(__file__).resolve().parent.parent
BETS_PATH = argo_paths.BETS_PATH  # single source of truth (see argo_paths)

TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Curated frontier Bet pool.
# Hand-written for the manual run. The first Bet is the one named in
# ARGO_ARCHITECTURE.md. Argo picks one per week (oldest-unbet first, so it
# rotates rather than repeating). Add Bets here; do not over-engineer.
# ---------------------------------------------------------------------------

# Bet fields:
#   insight   - the observation Argo leads with (the "I've been watching
#               something" hook). Several short lines; one thought each.
#   concept / concept_emoji - the named bet
#   structures - bullet list shown under the bet (or None)
#   artifact  - the public thing produced
#   effort    - HUMAN label only: "An evening" / "A weekend" / "A few days" / "A week"
#   upside    - the frontier-scale payoff Argo closes on (not a score)
#   confidence / potential_upside / reason / energy_prediction - INTERNAL
#     (logged for analysis; never shown in the message)

BET_POOL = [
    {
        "id": "B-001",
        "bet": "Cognitive Operators",
        "concept_emoji": "🧠",
        "insight": [
            "Everyone's still arguing about which model is best.",
            "But subagents quietly changed the question.",
            "The variable that matters now may not be the model.",
            "It may be the organizational structure wrapped around it.",
            "Almost nobody is measuring that.",
        ],
        "structures": ["Single Agent", "Researcher", "Researcher + Critic"],
        "build_this_week": "Run the same frontier signal through three structures "
                           "and observe how the thinking changes.",
        "artifact": "A public “Agent Structure → Thinking Mode” benchmark.",
        "effort": "A weekend.",
        "upside": "If the hypothesis is true, future AI systems may be designed "
                  "around desired cognitive operations rather than tasks. That's a "
                  "bigger frontier than prompt engineering.",
        "confidence": "Medium",
        "potential_upside": "High",
        "reason": "Could reveal organizational laws of intelligence.",
        "energy_prediction": 8,
    },
    {
        "id": "B-002",
        "bet": "Theory, Tested Three More Times",
        "concept_emoji": "🔬",
        "insight": [
            "F-001 said something quietly bold:",
            "agent structure may change *how* a system thinks, not just what it says.",
            "But it's only been seen on two signals.",
            "A pattern that holds twice is a coincidence wearing a theory's clothes.",
        ],
        "structures": None,
        "build_this_week": "Run three fresh frontier signals through the Researcher "
                           "structure and check whether it keeps producing theory.",
        "artifact": "An updated cognitive-operations table + a one-page claim, with "
                    "evidence from signals #3, #4 and #5.",
        "effort": "A few days.",
        "upside": "If it holds, a tentative finding becomes a defensible frontier "
                  "theory — the kind other builders cite.",
        "confidence": "Low",
        "potential_upside": "High",
        "reason": "Turns a tentative finding into a defensible frontier theory.",
        "energy_prediction": 7,
    },
    {
        "id": "B-003",
        "bet": "Run Argo On Yourself",
        "concept_emoji": "⚓",
        "insight": [
            "Argo's most novel idea is that *energy* — not correctness — is the "
            "signal worth optimizing.",
            "That's just a hypothesis right now.",
            "The fastest way to test a hypothesis about you is to run it on you.",
        ],
        "structures": None,
        "build_this_week": "Use Argo every Monday for a few weeks; quietly log "
                           "whether the energy you reported predicted what you "
                           "actually built.",
        "artifact": "A short note: “Does energy predict shipping?” with your "
                    "first weeks of data.",
        "effort": "An evening, then a few minutes a week.",
        "upside": "If energy predicts shipping, Argo has found its optimization "
                  "target — and you've validated it before building anything heavier.",
        "confidence": "High",
        "potential_upside": "Medium",
        "reason": "Validates Argo's core optimization target before building more.",
        "energy_prediction": 6,
    },
]

# Human effort labels Argo is allowed to use (no hour counts).
EFFORT_LABELS = ("An evening", "A weekend", "A few days", "A week")


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
    """Render a Bet as a Telegram message in Argo's frontier-scout voice.

    Leads with the insight, presents the project as a bet, closes on the upside.
    No markdown headings, short paragraphs. Does NOT ask for energy — that is a
    separate cognitive moment, prompted later by ask_energy().
    This is the single weekly-bet formatter in the repo; seas_demo.py calls it.
    """
    # Insight: one short line per thought, each its own paragraph.
    insight = "\n\n".join(bet["insight"])

    parts = [
        "⚓ Argo",
        "I've been watching something.",
        insight,
        "This week's bet:",
        f"{bet['concept_emoji']} {bet['bet']}",
    ]

    if bet.get("structures"):
        bullets = "\n".join(f"• {s}" for s in bet["structures"])
        parts.append(
            f"Run the same frontier signal through:\n\n{bullets}\n\n"
            "and observe how the thinking changes."
        )
    else:
        parts.append(bet["build_this_week"])

    parts.append(f"Artifact:\n{bet['artifact']}")
    parts.append(f"Effort:\n{bet['effort']}")
    parts.append(f"Potential upside:\n{bet['upside']}")

    return "\n\n".join(parts) + "\n"


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
