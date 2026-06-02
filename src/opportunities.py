import json
from score import score_signal, qualifies

with open("data/signals.json", "r") as f:
    signals = json.load(f)

opportunities = []

for signal in signals:
    weighted_score = score_signal(signal)

    opportunity = {
        "title": signal["title"],
        "category": signal.get("category", ""),
        "capability": signal.get("possible_capability_unlocked", ""),
        "weighted_score": weighted_score,
        "qualifies": qualifies(signal, weighted_score),
        "scores": signal["scores"]
    }

    opportunities.append(opportunity)

opportunities.sort(key=lambda x: x["weighted_score"], reverse=True)

with open("data/opportunities.json", "w") as f:
    json.dump(opportunities, f, indent=2)

print("\n🌊 Ranked Opportunities\n")

for opp in opportunities:
    status = "QUALIFIES" if opp["qualifies"] else "DOES NOT QUALIFY"
    print(f"{opp['title']}: {opp['weighted_score']} — {status}")

print("\nSaved: data/opportunities.json")
