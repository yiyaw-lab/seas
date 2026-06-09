import json
from datetime import datetime

name = input("Capability Name: ")
description = input("Description: ")

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

capability = {
    "name": name,
    "status": "aware",
    "description": description,
    "signals": [],
    "experiments": [],
    "artifacts": [],
    "last_updated": datetime.now().strftime("%Y-%m-%d")
}

capabilities.append(capability)

with open("data/capabilities.json", "w") as f:
    json.dump(capabilities, f, indent=2)

print(f"\nAdded capability: {name}")
