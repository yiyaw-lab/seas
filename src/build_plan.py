from datetime import datetime
from pathlib import Path

today = datetime.now().strftime("%Y-%m-%d")

active_file = Path(f"runs/{today}-active-experiment.md")

if not active_file.exists():
    print("No active experiment found.")
    exit(1)

plan = f"""# Build Plan — {today}

## Day 1
- Define the smallest useful version
- Write success criteria

## Day 2
- Create project structure
- Build the core loop

## Day 3
- Test with one real use case

## Day 4
- Fix major issues

## Day 5
- Produce artifact

## Day 6
- Document findings

## Day 7
- Reflection
- Capability gained
- What to improve next
"""

output_file = Path(f"runs/{today}-build-plan.md")
output_file.write_text(plan)

print(f"Saved: {output_file}")
