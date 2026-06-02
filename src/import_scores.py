import json

signal_title = input("Signal title: ")

durability = int(input("Durability: "))
leverage = int(input("Leverage: "))
alignment = int(input("Alignment: "))
accessibility = int(input("Accessibility: "))
novelty = int(input("Novelty: "))

with open("data/signals.json", "r") as f:
    signals = json.load(f)

updated = False

for signal in signals:
    if signal["title"] == signal_title:
        signal["scores"] = {
            "durability": durability,
            "leverage": leverage,
            "alignment": alignment,
            "accessibility": accessibility,
            "novelty": novelty
        }
        updated = True
        break

if not updated:
    print(f"Signal not found: {signal_title}")
    exit(1)

with open("data/signals.json", "w") as f:
    json.dump(signals, f, indent=2)

print(f"Updated scores for: {signal_title}")
