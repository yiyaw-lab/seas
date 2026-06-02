"""
SEAS proof-of-concept demo.

Reads existing repo artifacts and walks the current north-star loop:

    Signal -> Opportunity -> Experiment -> Finding -> Theory

Produces two outputs:

1. demo/SEAS_DEMO_REPORT.md      full research-loop report
2. demo/weekly_project_message.md  concise weekly recommendation
                                   (text-message style; not yet wired to SMS/Telegram)

Standalone: imports nothing from seas.py and is not invoked by it.
Run with:  python src/seas_demo.py
"""

from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"

TODAY = datetime.now().strftime("%Y-%m-%d")


def read(rel):
    """Read a repo artifact if present; return a placeholder note otherwise."""
    path = ROOT / rel
    if path.exists():
        return path.read_text().strip()
    return f"(missing artifact: {rel})"


# ---------------------------------------------------------------------------
# The research loop, traced from real repo artifacts.
# Each stage names the source file it is grounded in.
# ---------------------------------------------------------------------------

LOOP = {
    "signal": {
        "title": "Claude Code Subagents",
        "source": "data/signals.json",
        "summary": "Specialized subagents collaborating on tasks. A frontier "
                   "development in how AI work gets organized rather than how "
                   "models get bigger.",
    },
    "opportunity": {
        "title": "Treat organizational structure as a tunable variable",
        "source": "experiments/SEAS-001-agent-organization-lab.md",
        "statement": "If subagents exist, the open frontier question is not "
                     "'which model?' but 'which organization?'. The opening: "
                     "run the same task through different agent structures and "
                     "measure whether structure changes the thinking produced.",
        "capability_gain": "Designing agent systems around desired thinking "
                            "modes, not just task completion.",
    },
    "experiment": {
        "title": "SEAS-001 Agent Organization Lab",
        "source": "experiments/SEAS-001-agent-organization-lab.md + results/agent_organization_lab/",
        "design": "Give the same frontier signal to different structures "
                  "(Single Agent, Researcher + Critic) and compare outputs.",
        "result": "Single Agent produced a benchmarking opportunity. "
                  "Researcher + Critic produced a theory-generation opportunity "
                  "(\"Organizational Laws of Intelligence\"). Adding a critic "
                  "changed the level of abstraction, not just the quality.",
    },
    "finding": {
        "title": "F-001: Agent structures may function as cognitive operators",
        "source": "findings/F-001-cognitive-operators.md",
        "statement": "Agent organizational structures may systematically "
                     "influence the *type* of thinking produced. Researcher "
                     "structure consistently produced Theory Thinking across "
                     "two different frontier signals.",
        "confidence": "Low (only two signals tested).",
    },
    "theory": {
        "title": "Organizational structure as a cognitive operator",
        "source": "results/agent_organization_lab/emerging_theory.md",
        "claim": "Agent systems can be designed around desired thinking modes "
                 "rather than merely task completion. Structure is a lever on "
                 "cognition: Single Agent -> Benchmark Thinking, Researcher -> "
                 "Theory Thinking, Critic -> Assumption-Challenging Thinking, "
                 "Researcher + Critic -> Meta-Theory Thinking.",
        "open_question": "Can cognitive operations be intentionally composed "
                         "through organizational design?",
    },
}


# ---------------------------------------------------------------------------
# Output 1: full research-loop report
# ---------------------------------------------------------------------------

def build_report():
    s = LOOP["signal"]
    o = LOOP["opportunity"]
    e = LOOP["experiment"]
    f = LOOP["finding"]
    t = LOOP["theory"]

    return f"""# SEAS Demo Report — {TODAY}

> Proof-of-concept walk of the SEAS north-star loop, traced from real repo
> artifacts. See [README](../README.md).

```
Signal → Opportunity → Experiment → Finding → Theory
```

---

## 1. Signal

**{s['title']}**
_source: {s['source']}_

{s['summary']}

## 2. Opportunity

**{o['title']}**
_source: {o['source']}_

{o['statement']}

Capability gain: {o['capability_gain']}

## 3. Experiment

**{e['title']}**
_source: {e['source']}_

Design: {e['design']}

Result: {e['result']}

## 4. Finding

**{f['title']}**
_source: {f['source']}_

{f['statement']}

Confidence: {f['confidence']}

## 5. Theory

**{t['title']}**
_source: {t['source']}_

{t['claim']}

Open question: {t['open_question']}

---

## Loop Summary

| Stage | Result |
|---|---|
| Signal | {s['title']} |
| Opportunity | {o['title']} |
| Experiment | {e['title']} |
| Finding | {f['title']} |
| Theory | {t['title']} |

This is one complete pass of SEAS: a change in the world became a framed
opening, the opening became a buildable test, the test produced evidence,
and the evidence generalized into a claim.
"""


# ---------------------------------------------------------------------------
# Output 2: weekly project message (text-message style)
# ---------------------------------------------------------------------------
# Argo owns the weekly bet voice. There is exactly ONE bet formatter in the
# repo (argo.format_bet); this demo calls it so the Telegram message and Argo's
# own output can never drift apart.

import argo  # noqa: E402  (same-dir import, mirrors main.py importing experiment)


def build_weekly_message():
    bet = argo.select_bet(argo.load_bets_log())
    return argo.format_bet(bet)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main():
    DEMO.mkdir(exist_ok=True)

    report = build_report()
    message = build_weekly_message()

    report_path = DEMO / "SEAS_DEMO_REPORT.md"
    message_path = DEMO / "weekly_project_message.md"

    report_path.write_text(report)
    message_path.write_text(message)

    print("\n🌊 SEAS Demo\n")
    print("Loop traced from real repo artifacts:\n")
    print(f"  Signal      → {LOOP['signal']['title']}")
    print(f"  Opportunity → {LOOP['opportunity']['title']}")
    print(f"  Experiment  → {LOOP['experiment']['title']}")
    print(f"  Finding     → {LOOP['finding']['title']}")
    print(f"  Theory      → {LOOP['theory']['title']}")
    print("\nGenerated:")
    print(f"  - {report_path.relative_to(ROOT)}")
    print(f"  - {message_path.relative_to(ROOT)}  (weekly text preview)")
    print("\n✅ Demo complete.\n")


if __name__ == "__main__":
    main()
