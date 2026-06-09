import json

def determine_status(capability):
    if len(capability.get("artifacts", [])) > 0:
        return "applied"

    if len(capability.get("experiments", [])) > 0:
        return "exploring"

    if len(capability.get("signals", [])) > 0:
        return "aware"

    return "unknown"

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

for capability in capabilities:
    old_status = capability.get("status", "unknown")
    new_status = determine_status(capability)

    capability["status"] = new_status

    print(f"{capability['name']}: {old_status} → {new_status}")

with open("data/capabilities.json", "w") as f:
    json.dump(capabilities, f, indent=2)
