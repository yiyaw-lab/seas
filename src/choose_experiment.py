from datetime import datetime
from pathlib import Path

today = datetime.now().strftime("%Y-%m-%d")
run_file = Path(f"runs/{today}-experiment.md")
active_file = Path(f"runs/{today}-active-experiment.md")

if not run_file.exists():
    print(f"ERROR: Run file not found: {run_file}")
    exit(1)

print(f"Open this file and choose an option:")
print(run_file)

choice = input("\nWhich option do you choose? 1, 2, or 3: ")

if choice not in ["1", "2", "3"]:
    print("ERROR: Choose 1, 2, or 3.")
    exit(1)

content = run_file.read_text()

active = f"""# Active SEAS Experiment — {today}

Chosen Option: {choice}

Source File: {run_file}

---

{content}
"""

active_file.write_text(active)

print(f"\nSaved active experiment: {active_file}")
