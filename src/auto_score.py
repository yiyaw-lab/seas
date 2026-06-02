import json
from pathlib import Path

with open("prompts/score_signal.md", "r") as f:
    scoring_prompt = f.read()

with open("data/signals.json", "r") as f:
    signals = json.load(f)

unscored = [
    s for s in signals
    if sum(s["scores"].values()) == 0
]

output_dir = Path("runs/scoring")
output_dir.mkdir(parents=True, exist_ok=True)

print(f"\nFound {len(unscored)} unscored signals.\n")

for signal in unscored:
    full_prompt = f"""{scoring_prompt}

Signal to score:

Title: {signal['title']}
Source: {signal.get('source', '')}
Category: {signal.get('category', '')}
Summary: {signal.get('summary', '')}
Capability Unlocked: {signal.get('possible_capability_unlocked', '')}
"""

    filename = output_dir / f"{signal['title'].replace(' ', '_')}.md"

    with open(filename, "w") as f:
        f.write(full_prompt)

    print(f"Saved: {filename}")
