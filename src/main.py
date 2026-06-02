import json
from datetime import datetime
from experiment import generate_experiment

def format_list(items):
    return "\n".join([f"- {item}" for item in items])

with open("data/opportunities.json", "r") as f:
    opportunities = json.load(f)

winner = next(
    (o for o in opportunities if o["qualifies"]),
    None
)

if winner is None:
    print("No opportunity cleared the threshold.")
    exit()

experiment = generate_experiment({
    "title": winner["title"],
    "possible_capability_unlocked": winner["capability"]
})

print("\n🏆 Selected Opportunity")
print(f"{winner['title']}")
print(f"Score: {winner['weighted_score']}")

today = datetime.now().strftime("%Y-%m-%d")

output = f"""# SEAS Run — {today}

## Selected Opportunity

{winner['title']}

Score: {winner['weighted_score']}

## Capability

{winner['capability']}

## Experiment

{experiment['title']}
"""

filename = f"runs/{today}-experiment.md"

with open(filename, "w") as f:
    f.write(output)

print(f"\nSaved: {filename}")
