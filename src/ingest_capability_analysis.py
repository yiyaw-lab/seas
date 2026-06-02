import json
from datetime import datetime

with open("runs/capability_analysis/latest.json", "r") as f:
    analysis = json.load(f)

capability_name = analysis["capability_unlocked"]

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

existing = next(
    (c for c in capabilities if c["name"] == capability_name),
    None
)

today = datetime.now().strftime("%Y-%m-%d")

if existing:
    if analysis["signal_title"] not in existing["signals"]:
        existing["signals"].append(
            analysis["signal_title"]
        )

    existing["last_updated"] = today

    print(f"Updated capability: {capability_name}")

else:
    capabilities.append({
        "name": capability_name,
        "status": "aware",
        "description": analysis["why_it_matters"],
        "signals": [
            analysis["signal_title"]
        ],
        "experiments": [],
        "artifacts": [],
        "last_updated": today
    })

    print(f"Created capability: {capability_name}")

with open("data/capabilities.json", "w") as f:
    json.dump(capabilities, f, indent=2)
