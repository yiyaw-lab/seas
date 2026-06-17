"""Rehearse panel eval — does a bigger, more model-diverse adversary panel surface
MORE distinct failure modes? Tests the 3 > 2 > 1 hypothesis cleanly.

v2 rationale: scoring the JUDGE's hardened plan was confounded -- a bigger panel makes
the judge KILL weak bets more often (no plan to score), which is the panel WORKING but
tanked a "plan quality" rubric. The unconfounded signal is the PANEL's raw output:
distinct, concrete failure modes surfaced. For each bet, the real adversaries run at
panel sizes 1/2/3 (critic; +user; +ops, each a DISTINCT model), and an INDEPENDENT
extractor (haiku -- not a panelist) lists the DEDUPED distinct failure modes. More
panelists should surface >= as many; the test is monotonic coverage across bets.

Run:  PYTHONPATH=src python3 src/rehearse_panel_eval.py
"""

import json
import os

import argo_observe as observe
import argo_rehearse as r

EXTRACTOR_MODEL = os.environ.get("EB3_SCORER_MODEL") or "claude-haiku-4-5"
ROLES_BY_SIZE = {1: ["critic"], 2: ["critic", "user"], 3: ["critic", "user", "ops"]}

BETS = [
    {"id": "B1-changelog", "text": (
        "A CLI tool that auto-generates a project changelog from git history using an "
        "LLM: groups commits by type, drafts release notes, opens a PR with the update.")},
    {"id": "B2-thread-summary", "text": (
        "A browser extension that summarizes a long X/Twitter thread into 3 plain "
        "bullets inline, with one-click expand-to-full and a saved-summaries list.")},
    {"id": "B3-citation-watch", "text": (
        "A service that watches arXiv daily and emails a researcher a weekly digest of "
        "new papers citing their work, ranked by relevance, one line why-it-matters each.")},
    {"id": "B4-standup-bot", "text": (
        "A Slack bot that auto-drafts each engineer's daily standup from their last 24h "
        "of GitHub activity and posts it to the team channel for a quick edit.")},
    {"id": "B5-receipt-tracker", "text": (
        "A mobile app that scans receipts with the camera, extracts vendor/amount/date "
        "via OCR+LLM, auto-categorizes expenses, and exports a monthly CSV.")},
]

EXTRACT_SYSTEM = ("You extract risks from adversarial critiques. Be precise and merge "
                  "duplicates ruthlessly.")
EXTRACT_PROMPT = """Below are adversarial critiques of a project bet. Extract a DEDUPLICATED list of DISTINCT, CONCRETE failure modes / risks raised (merge near-duplicates across critiques; keep only real, specific risks, not generic caution).

Output ONLY a JSON array of short strings, one per distinct failure mode.

CRITIQUES:
{critiques}
"""


def run_panel_critiques(bet_text, size, assigned):
    """Run `size` adversaries (distinct models) -> combined critique text."""
    parts = []
    for role in ROLES_BY_SIZE[size]:
        prompt = r._adversary_prompt(r.ADVERSARIES[role], bet_text)
        try:
            c = r._call(r.ADVERSARY_SYSTEM, prompt, assigned[role], temperature=0.4).strip()
        except Exception as exc:
            c = f"(adversary {role} failed: {type(exc).__name__})"
        parts.append(f"[{role} / {assigned[role]}]\n{c}")
    return "\n\n".join(parts)


def _parse_list(raw):
    s, e = raw.find("["), raw.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            arr = json.loads(raw[s:e + 1])
            if isinstance(arr, list):
                return [str(x) for x in arr if str(x).strip()]
        except (ValueError, json.JSONDecodeError):
            pass
    # fallback: count non-empty, list-looking lines
    lines = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip(" -*\t")]
    return lines


def count_failure_modes(critiques):
    """Independent extractor -> count of distinct failure modes. One retry on empty."""
    for _ in range(2):
        raw = observe.generate_observations(
            f"{EXTRACT_SYSTEM}\n\n{EXTRACT_PROMPT.format(critiques=critiques)}",
            EXTRACTOR_MODEL, temperature=0)
        items = _parse_list(raw)
        if items:
            return len(items)
    return 0


def main():
    assigned = r._assign_adversary_models(r.ADVERSARIES)
    print(f"panel: {assigned} | extractor: {EXTRACTOR_MODEL}\n")
    rows = []
    for bet in BETS:
        counts = {}
        for size in (1, 2, 3):
            critiques = run_panel_critiques(bet["text"], size, assigned)
            counts[size] = count_failure_modes(critiques)
        rows.append((bet["id"], counts))
        print(f"{bet['id']:20} failure_modes  1={counts[1]:2}  2={counts[2]:2}  3={counts[3]:2}")

    print("\n=== averages by panel size ===")
    for size in (1, 2, 3):
        vals = [c[size] for (_, c) in rows]
        print(f"panel={size}: avg_distinct_failure_modes={sum(vals)/len(vals):.2f}")

    print("\n=== per-bet monotonicity (3 >= 2 >= 1 and 3 > 1) ===")
    mono = nondec = 0
    for bid, c in rows:
        strict = c[3] >= c[2] >= c[1] and c[3] > c[1]
        mono += strict
        nondec += (c[3] >= c[1])
        print(f"{bid:20} 1={c[1]:2} 2={c[2]:2} 3={c[3]:2}  {'UP' if strict else 'flat/mixed'}")
    print(f"\nstrictly-monotonic-up bets: {mono}/{len(rows)}; "
          f"3>=1 (coverage non-decreasing): {nondec}/{len(rows)}")
    json.dump([(b, c) for (b, c) in rows], open("data/rehearse_panel_eval.json", "w"),
              indent=2)
    print("wrote data/rehearse_panel_eval.json")


if __name__ == "__main__":
    main()
