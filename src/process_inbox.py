"""
SEAS signal inbox — merge manually-submitted signals into signals.json.

Drop signal titles (one per line, prefixed with "- ") into inbox/signals.md
and run:  python src/process_inbox.py

Dedupes by title and merges into the existing pool. Clears the inbox after
processing and leaves a datestamp. Useful alongside fetch_signals.py for
injecting signals you spotted manually that didn't appear in the feeds.
"""
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import argo_paths
import argo_store

INBOX_PATH = ROOT / "inbox" / "signals.md"


def process(inbox_path=None, signals_path=None):
    """Merge inbox signals into signals.json. Returns (added, skipped)."""
    inbox_path = Path(inbox_path or INBOX_PATH)
    signals_path = Path(signals_path or argo_paths.SIGNALS_PATH)

    if not inbox_path.exists():
        return 0, 0

    lines = inbox_path.read_text().splitlines()
    incoming = [
        line[2:].strip()
        for line in lines
        if line.strip().startswith("- ") and line.strip()[2:].strip()
    ]

    signals = argo_store.load_json(signals_path, [])
    existing_titles = {s["title"] for s in signals}

    added = 0
    for title in incoming:
        if title not in existing_titles:
            signals.append({
                "title": title,
                "source": "inbox",
                "category": "",
                "summary": "",
                "link": "",
                "possible_capability_unlocked": "",
                "scores": {
                    "durability": 0, "leverage": 0, "alignment": 0,
                    "accessibility": 0, "novelty": 0,
                },
            })
            existing_titles.add(title)
            added += 1

    skipped = len(incoming) - added
    if added:
        argo_store.save_json(signals_path, signals)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inbox_path.write_text(
        f"# SEAS Signal Inbox\n\n## Unprocessed Signals\n\n"
        f"## Last Processed\n\n{today}\n\nAdded: {added}\n"
        f"Skipped duplicates: {skipped}\n"
    )
    return added, skipped


def main():
    added, skipped = process()
    if added or skipped:
        print(f"Added {added} signal(s), skipped {skipped} duplicate(s).")
    else:
        print("Inbox empty or not found — nothing to process.")


if __name__ == "__main__":
    main()
