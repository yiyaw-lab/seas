import json
from datetime import datetime
from experiment import generate_experiments

today = datetime.now().strftime("%Y-%m-%d")

with open("data/opportunities.json", "r") as f:
    opportunities = json.load(f)

winner = next(
    (o for o in opportunities if o["qualifies"]),
    None
)

if winner is None:
    print("No opportunity cleared the threshold.")
    exit()

experiments = generate_experiments({
    "title": winner["title"],
    "possible_capability_unlocked": winner["capability"]
})

print("\n🏆 Selected Opportunity")
print(winner["title"])
print(f"Score: {winner['weighted_score']}")

output = f"""# SEAS Run — {today}

## Selected Opportunity

{winner['title']}

Score: {winner['weighted_score']}

## Experiment Options

"""

for i, experiment in enumerate(experiments, start=1):
    output += f"""
### Option {i}: {experiment['type']}

Title:
{experiment['title']}

Capability:
{experiment['capability']}

Artifact:
{experiment['artifact']}

Completion:
{experiment['completion']}

"""

filename = f"runs/{today}-experiment.md"

with open(filename, "w") as f:
    f.write(output)

print(f"\nSaved: {filename}")
