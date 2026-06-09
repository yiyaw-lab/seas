import json

capability_name = input("Capability: ")
artifact_name = input("Artifact: ")

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

found = False

for capability in capabilities:
    if capability["name"] == capability_name:

        if artifact_name not in capability["artifacts"]:
            capability["artifacts"].append(artifact_name)

        found = True
        break

if not found:
    print("Capability not found.")
    exit(1)

with open("data/capabilities.json", "w") as f:
    json.dump(capabilities, f, indent=2)

print(f"Linked artifact to {capability_name}")
