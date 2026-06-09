import json

STATUS_PRIORITY = {
    "aware": 1,
    "exploring": 2,
    "applied": 3,
    "transferable": 4
}

with open("data/capabilities.json", "r") as f:
    capabilities = json.load(f)

candidates = [
    c for c in capabilities
    if c["status"] != "transferable"
]

if not candidates:
    print("All tracked capabilities are transferable.")
    exit()

candidates.sort(
    key=lambda c: STATUS_PRIORITY.get(c["status"], 0)
)

recommended = candidates[0]

print("\n🌊 SEAS Capability Recommendation\n")

print(f"Capability: {recommended['name']}")
print(f"Status: {recommended['status']}")
print(f"Signals: {len(recommended.get('signals', []))}")
print(f"Experiments: {len(recommended.get('experiments', []))}")
print(f"Artifacts: {len(recommended.get('artifacts', []))}")

print("\nNext Goal:")

if recommended["status"] == "aware":
    print("Create an experiment.")
elif recommended["status"] == "exploring":
    print("Produce an artifact.")
elif recommended["status"] == "applied":
    print("Create transfer evidence by applying this capability to a different project.")
else:
    print("Clarify next evidence needed.")
