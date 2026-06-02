import json
from datetime import datetime

INBOX_PATH = "inbox/signals.md"

with open(INBOX_PATH, "r") as f:
    lines = f.readlines()

incoming_titles = []

for line in lines:
    line = line.strip()

    if line.startswith("- "):
        incoming_titles.append(line[2:])

with open("data/signals.json", "r") as f:
    existing = json.load(f)

existing_titles = {s["title"] for s in existing}

added = 0
skipped = 0

for title in incoming_titles:
    if title not in existing_titles:
        existing.append({
            "title": title,
            "source": "",
            "category": "",
            "summary": "",
            "possible_capability_unlocked": "",
            "scores": {
                "durability": 0,
                "leverage": 0,
                "alignment": 0,
                "accessibility": 0,
                "novelty": 0
            }
        })
        added += 1
    else:
        skipped += 1

with open("data/signals.json", "w") as f:
    json.dump(existing, f, indent=2)

today = datetime.now().strftime("%Y-%m-%d")

with open(INBOX_PATH, "w") as f:
    f.write(f"""# SEAS Signal Inbox

## Unprocessed Signals

## Last Processed

{today}

Added: {added}
Skipped duplicates: {skipped}
""")

print(f"Added {added} new signals.")
print(f"Skipped {skipped} duplicates.")
print("Inbox cleared.")
