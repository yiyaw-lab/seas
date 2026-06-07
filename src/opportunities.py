"""
SEAS opportunity ranker — scores signals on 5 dimensions and ranks them.

The scoring math (weighted formula + qualifies() gate) lives in score.py.
This module reads signals.json, ranks all signals, and writes opportunities.json
so seas_finding.py's _load_opportunity() picks the best-ranked signal to
investigate instead of the first one with a link.

Run:  python src/opportunities.py
"""
from pathlib import Path

import argo_paths
import argo_store
from score import score_signal, qualifies

ROOT = Path(__file__).resolve().parent.parent
OPPORTUNITIES_PATH = ROOT / "data" / "opportunities.json"


def build(signals_path=None, opportunities_path=None):
    """Rank all signals by weighted score; write to opportunities_path.
    Returns the ranked list (highest score first)."""
    signals_path = Path(signals_path or argo_paths.SIGNALS_PATH)
    opportunities_path = Path(opportunities_path or OPPORTUNITIES_PATH)

    signals = argo_store.load_json(signals_path, [])
    opps = []
    for signal in signals:
        weighted = score_signal(signal)
        opps.append({
            "title": signal["title"],
            "category": signal.get("category", ""),
            "capability": signal.get("possible_capability_unlocked", ""),
            "weighted_score": weighted,
            "qualifies": qualifies(signal, weighted),
            "scores": signal.get("scores", {}),
        })
    opps.sort(key=lambda x: x["weighted_score"], reverse=True)
    opportunities_path.parent.mkdir(parents=True, exist_ok=True)
    argo_store.save_json(opportunities_path, opps)
    return opps


def main():
    opps = build()
    print("\n🌊 Ranked Opportunities\n")
    for o in opps:
        status = "QUALIFIES" if o["qualifies"] else "does not qualify"
        print(f"  [{o['weighted_score']:.2f}] {o['title'][:65]} — {status}")
    qualifying = [o for o in opps if o["qualifies"]]
    print(f"\n{len(qualifying)} qualifying of {len(opps)} total.")
    if qualifying:
        print(f"🏆 Top: {qualifying[0]['title']}\n")


if __name__ == "__main__":
    main()
