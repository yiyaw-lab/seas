import json
from datetime import datetime
from experiment import generate_experiment

def format_list(items):
    return "\n".join([f"- {item}" for item in items])

def save_run(today, content):
    filename = f"runs/{today}-experiment.md"
    with open(filename, "w") as f:
        f.write(content)
    print(f"\nSaved: {filename}")

today = datetime.now().strftime("%Y-%m-%d")

with open("data/opportunities.json", "r") as f:
    opportunities = json.load(f)

winner = next(
    (o for o in opportunities if o["qualifies"]),
    None
)

if winner is None:
    print("\nNo opportunity cleared the threshold.")

    output = f"""# SEAS Run — {today}

## Decision

No opportunity cleared the action threshold.

## Fallback Recommendation

Do not start a new frontier experiment this cycle.

Choose one:

1. Deepen an existing project
2. Finish an unfinished artifact
3. Strengthen a foundational capability

## Why This Matters

SEAS is allowed to say no. The goal is not novelty. The goal is capability gain.
"""

    save_run(today, output)
    exit()

experiment = generate_experiment({
    "title": winner["title"],
    "possible_capability_unlocked": winner["capability"]
})

print("\n🏆 Selected Opportunity")
print(f"{winner['title']}")
print(f"Score: {winner['weighted_score']}")

output = f"""# SEAS Run — {today}

## Selected Opportunity

**{winner['title']}**

Score: {winner['weighted_score']}

Category: {winner.get('category', '')}

## Experiment Card

### Title

{experiment['title']}

### Source Signal

{experiment['source_signal']}

### Capability Created

{experiment['capability']}

### Why Now

{experiment['why_now']}

### Time Scope

{experiment['time_scope']}

### Artifact

{experiment['artifact']}

### Completion Condition

{experiment['completion']}

### Build Steps

{format_list(experiment['build_steps'])}

### Failure Risks

{format_list(experiment['failure_risks'])}

### Fallback Plan

{experiment['fallback_plan']}

### Reflection Prompt

{experiment['reflection_prompt']}

### Possible Public Output

{experiment['possible_public_output']}
"""

save_run(today, output)
