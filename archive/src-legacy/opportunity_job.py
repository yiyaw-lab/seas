from pathlib import Path

prompt = Path("prompts/opportunity_generator.md").read_text()
signal = Path("runs/test_signal.md").read_text()

job = f"""{prompt}

{signal}
"""

Path("runs/opportunity_job.md").write_text(job)

print("Saved: runs/opportunity_job.md")
