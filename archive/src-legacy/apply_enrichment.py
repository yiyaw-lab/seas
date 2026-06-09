import json

with open("runs/enrichment/latest.json", "r") as f:
    enrichment = json.load(f)

signal_title = enrichment["signal_title"]

with open("data/signals.json", "r") as f:
    signals = json.load(f)

updated = False

for signal in signals:
    if signal["title"] == signal_title:
        signal["source"] = enrichment["source"]
        signal["category"] = enrichment["category"]
        signal["summary"] = enrichment["summary"]
        signal["possible_capability_unlocked"] = enrichment["possible_capability_unlocked"]
        updated = True
        break

if not updated:
    print(f"ERROR: Signal not found: {signal_title}")
    exit(1)

with open("data/signals.json", "w") as f:
    json.dump(signals, f, indent=2)

print(f"Updated metadata for: {signal_title}")
