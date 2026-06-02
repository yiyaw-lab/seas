import json

with open("runs/llm_scores/latest.json", "r") as f:
    score_data = json.load(f)

signal_title = score_data["signal_title"]

with open("data/signals.json", "r") as f:
    signals = json.load(f)

updated = False

for signal in signals:
    if signal["title"] == signal_title:
        signal["scores"] = {
            "durability": score_data["durability"],
            "leverage": score_data["leverage"],
            "alignment": score_data["alignment"],
            "accessibility": score_data["accessibility"],
            "novelty": score_data["novelty"]
        }
        updated = True
        break

if not updated:
    print(f"ERROR: Signal not found: {signal_title}")
    exit(1)

with open("data/signals.json", "w") as f:
    json.dump(signals, f, indent=2)

print(f"Applied scores to: {signal_title}")
