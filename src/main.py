import json
from datetime import datetime
from score import score_signal, qualifies
from experiment import generate_experiment

def format_list(items):
    return "\n".join([f"- {item}" for item in items])

def main():
    print("🌊 SEAS v0.4\n")

    with open("data/signals.json", "r") as f:
        signals = json.load(f)

    scored_signals = []

    for signal in signals:
        weighted_score = score_signal(signal)
        signal["weighted_score"] = weighted_score
        signal["qualifies"] = qualifies(signal, weighted_score)
        scored_signals.append(signal)

    scored_signals.sort(key=lambda x: x["weighted_score"], reverse=True)

    winner = next((s for s in scored_signals if s["qualifies"]), None)

    if winner is None:
        print("No signal cleared the threshold.")
        return

    experiment = generate_experiment(winner)

    print("🏆 Selected Opportunity")
    print(f"{winner['title']}")
    print(f"Score: {winner['weighted_score']}\n")

    print("🧪 Recommended Experiment")
    print(f"Title: {experiment['title']}")
    print(f"Capability: {experiment['capability']}")
    print(f"Artifact: {experiment['artifact']}")

    today = datetime.now().strftime("%Y-%m-%d")

    output = f"""# SEAS Run — {today}

## Selected Opportunity

**{winner['title']}**

Score: {winner['weighted_score']}

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

    filename = f"runs/{today}-experiment.md"

    with open(filename, "w") as f:
        f.write(output)

    print(f"\nSaved: {filename}")

if __name__ == "__main__":
    main()
