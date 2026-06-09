import json

title = input("Title: ")
source = input("Source: ")
category = input("Category: ")
summary = input("Summary: ")
capability = input("Capability Unlocked: ")

signal = {
    "title": title,
    "source": source,
    "category": category,
    "summary": summary,
    "possible_capability_unlocked": capability,
    "scores": {
        "durability": 0,
        "leverage": 0,
        "alignment": 0,
        "accessibility": 0,
        "novelty": 0
    }
}

with open("data/signals.json", "r") as f:
    signals = json.load(f)

signals.append(signal)

with open("data/signals.json", "w") as f:
    json.dump(signals, f, indent=2)

print(f"\nAdded: {title}")
print(f"Total signals: {len(signals)}")
