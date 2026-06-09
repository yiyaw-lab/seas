import json

with open("prompts/generate_experiment.md", "r") as f:
    prompt = f.read()

with open("prompts/experiment_output_format.md", "r") as f:
    output_format = f.read()

with open("data/opportunities.json", "r") as f:
    opportunities = json.load(f)

winner = next(
    (o for o in opportunities if o["qualifies"]),
    None
)

if winner is None:
    print("No qualifying opportunity found.")
    exit()

full_prompt = f"""{prompt}

{output_format}

Signal:

Title: {winner['title']}
Category: {winner['category']}
Capability: {winner['capability']}
Weighted Score: {winner['weighted_score']}
"""

output_file = "runs/experiment_generation_job.md"

with open(output_file, "w") as f:
    f.write(full_prompt)

print(f"Saved: {output_file}")
