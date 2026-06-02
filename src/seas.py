import json
import subprocess
import sys

with open("data/signals.json", "r") as f:
    signals = json.load(f)

unscored = [
    s for s in signals
    if all(v == 0 for v in s["scores"].values())
]

print("\n🌊 SEAS Weekly Run\n")

subprocess.run(["python", "src/week.py"])

if unscored:
    print("\n❌ SEAS BLOCKED")
    print("Unscored signals detected:\n")

    for signal in unscored:
        print(f"- {signal['title']}")

    sys.exit(1)

print("\n✅ All signals scored.")

print("\nGenerating ranked opportunities...\n")
subprocess.run(
    ["python", "src/opportunities.py"],
    check=True
)

print("\nGenerating experiment recommendation...\n")
subprocess.run(
    ["python", "src/main.py"],
    check=True
)

print("\n✅ SEAS completed successfully.")
