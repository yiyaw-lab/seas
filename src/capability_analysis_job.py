import json
from pathlib import Path

with open("prompts/capability_analysis.md", "r") as f:
    prompt = f.read()

with open("data/signals.json", "r") as f:
    signals = json.load(f)

signal = signals[0]

job = f"""{prompt}

Signal:

Title: {signal['title']}
Source: {signal['source']}
Category: {signal['category']}
Summary: {signal['summary']}
"""

Path("runs/capability_analysis_job.md").write_text(job)

print("Saved: runs/capability_analysis_job.md")
