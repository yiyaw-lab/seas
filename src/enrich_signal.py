import json

with open("prompts/enrich_signal.md", "r") as f:
    prompt = f.read()

with open("data/signals.json", "r") as f:
    signals = json.load(f)

for signal in signals:
    if signal["summary"] == "":
        print("=" * 80)

        enrichment_prompt = f"""{prompt}

Signal:

Title: {signal['title']}
"""

        print(enrichment_prompt)
        print("=" * 80)
