import json
from datetime import datetime
from score import score_signal, qualifies
from experiment import generate_experiment

def main():
    print("🌊 SEAS v0.3\n")

    with open("data/signals.json", "r") as f:
        signals = json.load(f)

    scored_signals = []

    for signal in signals:
        weighted_score = score_signal(signal)

        signal["weighted_score"] = weighted_score
        signal["qualifies"] = qualifies(signal, weighted_score)

        scored_signals.append(signal)

    scored_signals.sort(
        key=lambda x: x["weighted_score"],
        reverse=True
    )

    winner = next(
        (s for s in scored_signals if s["qualifies"]),
        None
    )

    if winner is None:
        print("No signal cleared the threshold.")
        return

    experiment = generate_experiment(winner)

    print("🏆 Selected Opportunity")
    print(f"{winner['title']}")
    print(f"Score: {winner['weighted_score']}\n")

    print("🧪 Recommended Experiment")
    print(f"Title: {experiment['title']}")

    today = datetime.now().strftime("%Y-%m-%d")

    output = f"""# SEAS Run

Date: {today}

## Selected Opportunity

{winner['title']}

Score: {winner['weighted_score']}

## Recommended Experiment

Title: {experiment['title']}

Capability: {experiment['capability']}

Artifact: {experiment['artifact']}

Completion Condition:

{experiment['completion']}
"""

    filename = f"runs/{today}-experiment.md"

    with open(filename, "w") as f:
        f.write(output)

    print(f"\nSaved: {filename}")

if __name__ == "__main__":
    main()
