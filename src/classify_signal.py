import json

with open("prompts/classify_signal.md", "r") as f:
    prompt = f.read()

with open("data/signals.json", "r") as f:
    signals = json.load(f)

for signal in signals:
    classification_prompt = f"""{prompt}

Signal:

Title: {signal['title']}
Source: {signal.get('source', '')}
Category: {signal.get('category', '')}
Summary: {signal.get('summary', '')}
Capability Unlocked: {signal.get('possible_capability_unlocked', '')}
"""

    print("=" * 80)
    print(classification_prompt)
    print("=" * 80)
