import json

WEIGHTS = {
    "durability": 0.30,
    "leverage": 0.30,
    "alignment": 0.20,
    "accessibility": 0.10,
    "novelty": 0.10
}

ACTION_THRESHOLD = 4.0

def score_signal(signal):
    scores = signal["scores"]

    weighted = (
        scores["durability"] * WEIGHTS["durability"] +
        scores["leverage"] * WEIGHTS["leverage"] +
        scores["alignment"] * WEIGHTS["alignment"] +
        scores["accessibility"] * WEIGHTS["accessibility"] +
        scores["novelty"] * WEIGHTS["novelty"]
    )

    return round(weighted, 2)

def qualifies(signal, weighted_score):
    scores = signal["scores"]

    return (
        weighted_score >= ACTION_THRESHOLD
        and scores["durability"] >= 3
        and scores["leverage"] >= 3
        and scores["alignment"] >= 3
    )

def main():
    with open("data/signals.json", "r") as f:
        signals = json.load(f)

    scored_signals = []

    for signal in signals:
        weighted_score = score_signal(signal)
        signal["weighted_score"] = weighted_score
        signal["qualifies"] = qualifies(signal, weighted_score)
        scored_signals.append(signal)

    scored_signals.sort(key=lambda x: x["weighted_score"], reverse=True)

    print("\n🌊 SEAS Opportunity Scores\n")

    for signal in scored_signals:
        status = "QUALIFIES" if signal["qualifies"] else "DOES NOT QUALIFY"
        print(f"{signal['title']}: {signal['weighted_score']} — {status}")

    winner = next((s for s in scored_signals if s["qualifies"]), None)

    print("\n🏆 Selected Opportunity\n")

    if winner:
        print(f"{winner['title']} ({winner['weighted_score']})")
    else:
        print("No signal cleared the action threshold.")

if __name__ == "__main__":
    main()
