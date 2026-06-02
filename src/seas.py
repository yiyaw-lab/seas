import json
import subprocess

with open("data/signals.json", "r") as f:
    signals = json.load(f)

unscored = [
    s for s in signals
    if all(v == 0 for v in s["scores"].values())
]

print("\n🌊 SEAS Weekly Run\n")

subprocess.run(["python", "src/week.py"])

if unscored:
    print("\n⚠️ Some signals still need scoring.")
    print("Next command:")
    print("python src/auto_score.py")
else:
    print("\n✅ All signals scored.")
    print("\nGenerating ranked opportunities...\n")
    subprocess.run(["python", "src/opportunities.py"])

    print("\nGenerating experiment recommendation...\n")
    subprocess.run(["python", "src/main.py"])
