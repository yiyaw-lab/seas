"""Rehearse panel eval -- does model DIVERSITY (not just panel size) surface more
of a held-out risk set? Isolates the multi-MODEL thesis behind Decision_025.

The pinned study (.claude/until/rehearse-panel-model-diversity.md): at a FIXED
panel size of 3, a DIVERSE-model arm (claude-sonnet + gpt-5 + grok, one per role)
is compared against a SAME-model arm (one model x3 -- the negative control). Both
run the same three roles (critic/user/ops) on the same bets. An INDEPENDENT scorer
(haiku -- NOT a panelist, NOT the judge) marks, for each arm's combined critique
bundle, which of a per-bet HELD-OUT risk set it surfaced. The score is risk
COVERAGE (fraction of the held-out set covered), NOT raw failure-mode COUNT: count
has a critique-volume confound (a bigger/chattier panel trivially produces more
text). If diverse does not beat same, the multi-model premise is refuted and
Decision_025 should be revisited.

v3 changes vs the EB3 size-study this replaces:
  - FIXED size 3; varies the MODEL AXIS (diverse vs same), not panel size.
  - same-3 negative-control arm added (_assign_same_model).
  - COUNT metric replaced with risk_coverage(critiques, risk_set) against a
    per-bet held-out risk set.
  - argparse: --n N (subset of bets, for smoke) and --mock (offline, zero-spend:
    a deterministic fake provider runs the WHOLE pipeline with no network).

Run (real spend -- multi-model API):  PYTHONPATH=src python3 src/rehearse_panel_eval.py --n 1
Run (offline, zero spend):            PYTHONPATH=src python3 src/rehearse_panel_eval.py --mock --n 1
"""

import argparse
import json
import os
import re

import argo_observe as observe
import argo_rehearse as r

EXTRACTOR_MODEL = os.environ.get("EB3_SCORER_MODEL") or "claude-haiku-4-5"
ROLES = ["critic", "user", "ops"]  # fixed size-3 panel for both arms

# Each bet carries a HELD-OUT risk set: the concrete failure modes a strong panel
# SHOULD surface. Coverage = fraction of these the arm's critique bundle raises,
# as judged by the independent scorer. Keep them distinct and concrete (no generic
# caution) so the scorer can mark presence/absence cleanly.
BETS = [
    {"id": "B1-changelog", "text": (
        "A CLI tool that auto-generates a project changelog from git history using an "
        "LLM: groups commits by type, drafts release notes, opens a PR with the update."),
        "risks": [
            "commit messages are too terse or noisy for the LLM to group accurately",
            "hallucinated or wrong release notes get auto-opened as a PR with no review",
            "every existing changelog generator and conventional-commits tool already does this",
            "API cost and latency scale with repo history on large monorepos",
            "merge-commit and squash-history noise pollutes the grouping",
        ]},
    {"id": "B2-thread-summary", "text": (
        "A browser extension that summarizes a long X/Twitter thread into 3 plain "
        "bullets inline, with one-click expand-to-full and a saved-summaries list."),
        "risks": [
            "X DOM changes constantly and breaks the scraping selectors",
            "X terms of service or rate limits block automated thread extraction",
            "three bullets lose the nuance that made the thread worth reading",
            "users will not install yet another extension for a marginal time save",
            "summarization API cost per thread makes a free extension unsustainable",
        ]},
    {"id": "B3-citation-watch", "text": (
        "A service that watches arXiv daily and emails a researcher a weekly digest of "
        "new papers citing their work, ranked by relevance, one line why-it-matters each."),
        "risks": [
            "citation data is delayed or incomplete so new citations are missed",
            "relevance ranking is hard with no ground truth to tune against",
            "Google Scholar and Semantic Scholar already send citation alerts free",
            "the why-it-matters line is generic LLM filler researchers ignore",
            "deliverability: the weekly email lands in spam and is never read",
        ]},
    {"id": "B4-standup-bot", "text": (
        "A Slack bot that auto-drafts each engineer's daily standup from their last 24h "
        "of GitHub activity and posts it to the team channel for a quick edit."),
        "risks": [
            "GitHub activity is a poor proxy for what an engineer actually worked on",
            "auto-posting before the edit creates noise and embarrassing drafts",
            "engineers who did non-code work get an empty or wrong standup",
            "privacy concern: surfacing one person's commit cadence to the whole team",
            "teams already have standup rituals and will not adopt a bot for them",
        ]},
    {"id": "B5-receipt-tracker", "text": (
        "A mobile app that scans receipts with the camera, extracts vendor/amount/date "
        "via OCR+LLM, auto-categorizes expenses, and exports a monthly CSV."),
        "risks": [
            "OCR fails on faded, crumpled, or non-standard receipt formats",
            "wrong amount extraction silently corrupts the expense total",
            "Expensify and bank apps already do receipt scanning and categorization",
            "auto-categorization needs per-user training to be useful",
            "handling financial data raises privacy and storage-compliance burden",
        ]},
]

# --------------------------------------------------------------------------- #
# Arms: both run at FIXED size 3, same roles. They differ only on the MODEL axis.
# --------------------------------------------------------------------------- #

def assign_diverse(roles):
    """The DIVERSE arm: each role gets a genuinely distinct model (claude-sonnet +
    gpt-5 + grok by default) via the production assignment logic. Returns {role:
    model} or None if no provider key is available."""
    return r._assign_adversary_models(roles)


def assign_same(roles):
    """The SAME arm (negative control): all three roles share ONE model. Picks the
    first model the diverse arm would have used, so the comparison holds model
    QUALITY roughly fixed and varies only DIVERSITY. Returns {role: model} or None
    if no provider key is available."""
    diverse = r._assign_adversary_models(roles)
    if diverse is None:
        return None
    one = next(iter(diverse.values()))  # the role-order-first model
    return {role: one for role in roles}


def run_arm_critiques(bet_text, assigned):
    """Run the size-3 panel under one arm's {role: model} assignment -> combined
    critique text (role-tagged blocks). A role that errors yields a short note
    instead of sinking the arm."""
    parts = []
    for role in ROLES:
        prompt = r._adversary_prompt(r.ADVERSARIES[role], bet_text)
        try:
            c = r._call(r.ADVERSARY_SYSTEM, prompt, assigned[role],
                        temperature=0.4).strip()
        except Exception as exc:
            c = f"(adversary {role} failed: {type(exc).__name__})"
        parts.append(f"[{role} / {assigned[role]}]\n{c}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Metric: risk COVERAGE (not count). The independent scorer marks which held-out
# risks a critique bundle surfaces; coverage = covered / total.
# --------------------------------------------------------------------------- #

COVERAGE_SYSTEM = ("You judge whether an adversarial critique surfaced specific known "
                   "risks. You are independent: not one of the critics, not the judge.")
COVERAGE_PROMPT = """Below is a combined adversarial critique of a project bet, and a numbered HELD-OUT risk set. For EACH numbered risk, decide whether the critique surfaces it (raises that concrete concern, even in different words). Be strict: a generic caution does not count as surfacing a specific risk.

Output ONLY a JSON array of the integers of the risks that ARE surfaced (e.g. [1,3,4]). If none, output [].

HELD-OUT RISK SET:
{risk_list}

THE CRITIQUE:
{critiques}
"""


def _parse_int_list(raw):
    """Parse a JSON array of ints from the scorer reply; tolerant of surrounding text."""
    s, e = raw.find("["), raw.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            arr = json.loads(raw[s:e + 1])
            if isinstance(arr, list):
                out = []
                for x in arr:
                    try:
                        out.append(int(x))
                    except (TypeError, ValueError):
                        continue
                return out
        except (ValueError, json.JSONDecodeError):
            pass
    return []


def risk_coverage(critiques, risk_set):
    """Independent scorer -> fraction of `risk_set` the critique bundle surfaces.

    Returns (coverage_fraction, covered_indices). The scorer (haiku, a NON-panelist,
    NON-judge) is given the numbered held-out risks and the combined critique and
    returns which risk numbers are surfaced; coverage = |covered| / |risk_set|. One
    retry on an empty parse. An empty risk_set scores 0.0 (nothing to cover)."""
    if not risk_set:
        return 0.0, []
    risk_list = "\n".join(f"{i + 1}. {risk}" for i, risk in enumerate(risk_set))
    prompt = (f"{COVERAGE_SYSTEM}\n\n"
              + COVERAGE_PROMPT.format(risk_list=risk_list, critiques=critiques))
    covered = []
    for _ in range(2):
        raw = observe.generate_observations(prompt, EXTRACTOR_MODEL, temperature=0)
        nums = _parse_int_list(raw)
        # Keep only valid in-range, deduped indices.
        covered = sorted({n for n in nums if 1 <= n <= len(risk_set)})
        if covered:
            break
    return len(covered) / len(risk_set), covered


# --------------------------------------------------------------------------- #
# Offline MOCK mode: a deterministic fake provider so the WHOLE pipeline (both
# arms -> scorer -> coverage -> JSON -> comparison) runs with ZERO network/spend.
# Monkeypatches observe.generate_observations + observe.chat_with_mcp, the two
# call paths r._call dispatches to (generate_observations for openai/xai, and the
# scorer; chat_with_mcp for anthropic adversaries).
# --------------------------------------------------------------------------- #

def _mock_critique_for(model, role, bet):
    """A DETERMINISTIC fake critique. The surfaced risk subset is driven PRIMARILY by
    the MODEL (each model has a distinct 'blind-spot band' of the held-out set, like a
    real distinct mind), with the role adding one extra risk. Risks are written as
    'RISK#k: <text>' lines the fake scorer parses. Because coverage is model-keyed,
    THREE distinct models (the diverse arm) cover a WIDER union than ONE model repeated
    across the three roles (the same arm) -- so diverse>same is the expected and
    reproducible result, with no model call. Stable across runs/machines (uses a
    char-sum, not Python's salted hash())."""
    risks = bet["risks"]
    n = len(risks)
    if not n:
        return f"[{role}/{model}] no specific risks."
    # Model band: each model covers a small, model-specific contiguous slice of the
    # risk set (its "lens"). The slice is keyed by the MODEL only, NOT the role, so the
    # SAME model across the three roles covers ONE band (its repeated lens), while three
    # DISTINCT models cover the UNION of three partially-overlapping bands -- a wider set.
    # This is what makes diverse>same the reproducible result. The start offset folds in a
    # multiplier so the three default models (claude-sonnet/gpt-5/grok) land on DISTINCT
    # offsets (a plain char-sum collided gpt-5 and grok). Stable across machines (no
    # salted hash()).
    span = max(1, n // 2)  # each model's lens covers ~half the set
    cs = sum((i + 1) * ord(ch) for i, ch in enumerate(model))
    start = cs % n
    band = {(start + k) % n for k in range(span)}
    idxs = sorted(band)
    lines = [f"RISK#{i + 1}: {risks[i]}" for i in idxs]
    return f"[{role}/{model}] " + " ".join(lines)


def _install_mock_provider(monkey_holder):
    """Replace observe.generate_observations + observe.chat_with_mcp with
    deterministic fakes, and inject FAKE provider keys so the production assignment
    logic (_assign_adversary_models) resolves the diverse/same arms WITHOUT a real
    key. `monkey_holder` carries the current BET (set per bet by main) so the fakes
    know which risk set to draw from. Returns a restore() callable that undoes both
    the function swaps and the env-key injection. No network, no real keys, no spend."""
    real_gen = observe.generate_observations
    real_chat = observe.chat_with_mcp
    # Fake keys for all three providers so _assign_adversary_models yields 3 distinct
    # models (the real assignment path is exercised; the calls themselves are mocked).
    fake_keys = {"ANTHROPIC_API_KEY": "mock", "OPENAI_API_KEY": "mock",
                 "XAI_API_KEY": "mock"}
    saved_env = {k: os.environ.get(k) for k in fake_keys}
    os.environ.update(fake_keys)

    def fake_generate_observations(job, model, temperature=1.0):
        # The scorer routes here (haiku via generate_observations). Detect it by the
        # coverage prompt marker and return the surfaced risk numbers parsed straight
        # from the 'RISK#k' markers the fake critiques embedded -- a perfect,
        # deterministic stand-in for the independent scorer.
        if "HELD-OUT RISK SET" in job:
            critique_part = job.split("THE CRITIQUE:", 1)[-1]
            nums = sorted({int(tok) for tok in
                           re.findall(r"RISK#(\d+)", critique_part)})
            return json.dumps(nums)
        # Otherwise it's an adversary on an openai/xai model (gpt-5, grok): emit the
        # deterministic fake critique for the (model, role) of this call. Role is
        # carried in the prompt via the adversary instructions; recover it by which
        # ADVERSARIES instruction string the job contains.
        role = next((rl for rl in ROLES if r.ADVERSARIES[rl] in job), "critic")
        return _mock_critique_for(model, role, monkey_holder["bet"])

    def fake_chat_with_mcp(system, messages, model, mcp_servers=None,
                           max_tokens=1024, temperature=1.0,
                           return_tool_events=False, output_schema=None):
        # The anthropic adversary path (claude-sonnet) routes here. messages[0] is the
        # user prompt carrying the adversary instructions; recover the role from it.
        prompt = messages[0]["content"] if messages else ""
        role = next((rl for rl in ROLES if r.ADVERSARIES[rl] in prompt), "critic")
        return _mock_critique_for(model, role, monkey_holder["bet"])

    observe.generate_observations = fake_generate_observations
    observe.chat_with_mcp = fake_chat_with_mcp

    def restore():
        observe.generate_observations = real_gen
        observe.chat_with_mcp = real_chat
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return restore


# --------------------------------------------------------------------------- #
# Driver.
# --------------------------------------------------------------------------- #

def run_eval(bets, mock=False):
    """Run both arms over `bets` and return the result structure. In mock mode a
    deterministic fake provider is installed (zero spend); otherwise real model
    calls are made. Returns a dict:
        {"per_bet": [{id, diverse:{coverage,covered}, same:{...}}...],
         "diverse_avg", "same_avg", "diverse_beats_same"}.
    Returns None if no provider/keys are available (real mode only)."""
    monkey_holder = {"bet": None}
    restore = _install_mock_provider(monkey_holder) if mock else (lambda: None)
    try:
        diverse_assign = assign_diverse(ROLES)
        same_assign = assign_same(ROLES)
        if diverse_assign is None or same_assign is None:
            return None
        print(f"diverse arm: {diverse_assign}")
        print(f"same arm:    {same_assign}")
        print(f"scorer (independent): {EXTRACTOR_MODEL}\n")

        per_bet = []
        for bet in bets:
            monkey_holder["bet"] = bet
            d_crit = run_arm_critiques(bet["text"], diverse_assign)
            s_crit = run_arm_critiques(bet["text"], same_assign)
            d_cov, d_idx = risk_coverage(d_crit, bet["risks"])
            s_cov, s_idx = risk_coverage(s_crit, bet["risks"])
            per_bet.append({
                "id": bet["id"],
                "diverse": {"coverage": d_cov, "covered": d_idx},
                "same": {"coverage": s_cov, "covered": s_idx},
            })
            print(f"{bet['id']:20} diverse_cov={d_cov:.2f}  same_cov={s_cov:.2f}  "
                  f"{'DIVERSE>same' if d_cov > s_cov else ('tie' if d_cov == s_cov else 'same>diverse')}")
    finally:
        restore()

    d_avg = sum(b["diverse"]["coverage"] for b in per_bet) / len(per_bet)
    s_avg = sum(b["same"]["coverage"] for b in per_bet) / len(per_bet)
    return {
        "per_bet": per_bet,
        "diverse_avg": d_avg,
        "same_avg": s_avg,
        "diverse_beats_same": d_avg > s_avg,
    }


def main():
    ap = argparse.ArgumentParser(description="Rehearse panel model-diversity eval.")
    ap.add_argument("--n", type=int, default=len(BETS),
                    help="run only the first N bets (smoke).")
    ap.add_argument("--mock", action="store_true",
                    help="offline mode: deterministic fake provider, zero network/spend.")
    args = ap.parse_args()
    # Env knob as an alternative to --mock (per the placement triad: same offline path).
    mock = args.mock or os.environ.get("REHEARSE_PANEL_MOCK") == "1"

    bets = BETS[:max(1, args.n)]
    result = run_eval(bets, mock=mock)
    if result is None:
        print("No model providers available (set API keys, or use --mock). Aborting.")
        return

    print("\n=== averages by arm (risk coverage) ===")
    print(f"diverse-3: avg_coverage={result['diverse_avg']:.3f}")
    print(f"same-3:    avg_coverage={result['same_avg']:.3f}")
    print("\n=== diverse vs same (the binary done-check) ===")
    delta = result["diverse_avg"] - result["same_avg"]
    print(f"diverse {'>' if result['diverse_beats_same'] else '<='} same  "
          f"(delta={delta:+.3f})  ->  "
          f"{'DIVERSE WINS' if result['diverse_beats_same'] else 'NOT REFUTED-FREE: revisit Decision_025'}")

    out_path = "data/rehearse_panel_eval.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
