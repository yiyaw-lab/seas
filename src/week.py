import json

with open("data/signals.json", "r") as f:
    signals = json.load(f)

total = len(signals)

scored_signals = [
    s for s in signals
    if not all(v == 0 for v in s["scores"].values())
]

unscored_signals = [
    s for s in signals
    if all(v == 0 for v in s["scores"].values())
]

print("\n🌊 SEAS Weekly Status\n")

print(f"Total Signals: {total}")
print(f"Scored Signals: {len(scored_signals)}")
print(f"Unscored Signals: {len(unscored_signals)}")

if len(unscored_signals) > 0:
    print("\n⚠️ NEXT ACTION")
    print("Generate scoring jobs:")
    print("python src/auto_score.py")

    print("\nSignals awaiting scoring:")

    for signal in unscored_signals:
        print(f"- {signal['title']}")

else:
    print("\n✅ READY FOR DECISION")

    print("\nNEXT ACTION:")
    print("python src/main.py")
