# SEAS Demo Report — 2026-06-02

> Proof-of-concept walk of the SEAS north-star loop, traced from real repo
> artifacts. See [README](../README.md).

```
Signal → Opportunity → Experiment → Finding → Theory
```

---

## 1. Signal

**Claude Code Subagents**
_source: data/signals.json_

Specialized subagents collaborating on tasks. A frontier development in how AI work gets organized rather than how models get bigger.

## 2. Opportunity

**Treat organizational structure as a tunable variable**
_source: experiments/SEAS-001-agent-organization-lab.md_

If subagents exist, the open frontier question is not 'which model?' but 'which organization?'. The opening: run the same task through different agent structures and measure whether structure changes the thinking produced.

Capability gain: Designing agent systems around desired thinking modes, not just task completion.

## 3. Experiment

**SEAS-001 Agent Organization Lab**
_source: experiments/SEAS-001-agent-organization-lab.md + results/agent_organization_lab/_

Design: Give the same frontier signal to different structures (Single Agent, Researcher + Critic) and compare outputs.

Result: Single Agent produced a benchmarking opportunity. Researcher + Critic produced a theory-generation opportunity ("Organizational Laws of Intelligence"). Adding a critic changed the level of abstraction, not just the quality.

## 4. Finding

**F-001: Agent structures may function as cognitive operators**
_source: findings/F-001-cognitive-operators.md_

Agent organizational structures may systematically influence the *type* of thinking produced. Researcher structure consistently produced Theory Thinking across two different frontier signals.

Confidence: Low (only two signals tested).

## 5. Theory

**Organizational structure as a cognitive operator**
_source: results/agent_organization_lab/emerging_theory.md_

Agent systems can be designed around desired thinking modes rather than merely task completion. Structure is a lever on cognition: Single Agent -> Benchmark Thinking, Researcher -> Theory Thinking, Critic -> Assumption-Challenging Thinking, Researcher + Critic -> Meta-Theory Thinking.

Open question: Can cognitive operations be intentionally composed through organizational design?

---

## Loop Summary

| Stage | Result |
|---|---|
| Signal | Claude Code Subagents |
| Opportunity | Treat organizational structure as a tunable variable |
| Experiment | SEAS-001 Agent Organization Lab |
| Finding | F-001: Agent structures may function as cognitive operators |
| Theory | Organizational structure as a cognitive operator |

This is one complete pass of SEAS: a change in the world became a framed
opening, the opening became a buildable test, the test produced evidence,
and the evidence generalized into a claim.
