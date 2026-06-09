from pathlib import Path
from datetime import datetime

today = datetime.now().strftime("%Y-%m-%d")

prompt = Path("prompts/frontier_brief.md").read_text()
output_format = Path("prompts/frontier_brief_output.md").read_text()

full_prompt = f"""{prompt}

{output_format}
"""

output_path = Path(f"runs/{today}-frontier-brief-job.md")
output_path.write_text(full_prompt)

print(f"Saved: {output_path}")
