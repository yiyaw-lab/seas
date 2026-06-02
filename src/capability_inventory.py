import json

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

print("\n🌊 SEAS Capability Inventory\n")

for capability in capabilities:
    print(
        f"{capability['name']} "
        f"({capability['status']})"
    )

print(f"\nTotal Capabilities: {len(capabilities)}")
