"""
SEAS V3 orchestrator — full pipeline end-to-end:

  1. fetch_signals  — refresh the signal pool from curated frontier feeds
                      (lens-balanced: research / github / company)
  2. auto_score     — LLM-score any unscored signals on 5 dimensions
                      (durability / leverage / alignment / accessibility / novelty)
  3. opportunities  — rank by weighted score; surface qualifying signals
  4. seas_finding   — synthesize the top signal into a genuine cited finding
                      (seeds a world-model belief) or an honest dead-end probe

Replaces the V1 orchestrator, which stopped at a templated experiment card with
no evidence grounding. V3 produces real findings (cross-source convergence,
quote-fidelity gate) or honest probes — the model proposes, the gate disposes.

Run:  PYTHONPATH=src python src/seas.py
      PYTHONPATH=src python src/seas.py --dry-run
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import argo_paths
import argo_store
import fetch_signals
import opportunities
import seas_finding


def main():
    dry = "--dry-run" in sys.argv

    print("\n🌊 SEAS V3 Pipeline\n")

    # Step 1: refresh the signal pool from curated feeds
    print("── Step 1: fetch signals ──")
    fetch_signals.main()

    # Step 2: LLM-score any unscored signals
    signals = argo_store.load_json(argo_paths.SIGNALS_PATH, [])
    unscored = [s for s in signals
                if all(v == 0 for v in s.get("scores", {}).values())]
    if unscored:
        print(f"\n── Step 2: scoring {len(unscored)} unscored signal(s) ──")
        signals = seas_finding.auto_score_signals(signals, dry_run=dry)
        if not dry:
            argo_store.save_json(argo_paths.SIGNALS_PATH, signals)
    else:
        print(f"\n── Step 2: all {len(signals)} signal(s) already scored ──")

    # Step 3: rank opportunities by weighted score
    print("\n── Step 3: rank opportunities ──")
    opps = opportunities.build()
    qualifying = [o for o in opps if o.get("qualifies")]
    print(f"  {len(qualifying)} qualifying of {len(opps)} total")
    if qualifying:
        print(f"  top: [{qualifying[0]['weighted_score']:.2f}] "
              f"{qualifying[0]['title'][:60]}")

    # Step 4: synthesize the top-ranked opportunity
    print("\n── Step 4: synthesis ──")
    signal = seas_finding._load_opportunity()
    if signal is None:
        print("  no signal with a source link — nothing to investigate")
        return

    import firecrawl_client
    mode = "firecrawl" if firecrawl_client.is_enabled() else "stdlib"
    print(f"  investigating [{mode}]: {signal['title'][:70]}")
    result = seas_finding.investigate(signal, dry_run=dry)
    print(f"\n  {result}")

    print("\n✅ SEAS V3 complete.\n")


if __name__ == "__main__":
    main()
