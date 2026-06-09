import json

capability_name = input("Capability: ")
experiment_name = input("Experiment: ")

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

found = False

for capability in capabilities:
    if capability["name"] == capability_name:

        if experiment_name not in capability["experiments"]:
            capability["experiments"].append(
                experiment_name
            )

        found = True
        break

if not found:
    print("Capability not found.")
    exit(1)

with open("data/capabilities.json", "w") as f:
    json.dump(capabilities, f, indent=2)

print(f"Linked experiment to {capability_name}")
