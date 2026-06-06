# Design — Argo dispatches code changes to a coding agent (with an enrichment layer)

*Proposal, not built. Written 2026-06-05. The question: should Argo stop editing
its own repo via a raw GitHub token, and instead hand an enriched spec to a
coding agent (Claude Code) that actually reads, edits, tests, and PRs?*

---

## The problem with today's path

`propose_change(title, description, files_json)` takes **full file contents the
model writes blind** and PUTs them through the GitHub contents API. This is the
weakest possible way to edit code:

- No repo context — the model never reads the current file before rewriting it.
- No iteration — it can't run `py_compile`, a test, or the app to check itself.
- No conventions — it guesses structure and style.
- It's brittle in practice: this session alone produced repeated "what's the repo
  name" / "missing signals.json" flailing, all rooted in Argo trying to reason
  about a codebase it can't actually see.

It works for **trivial data edits** (add a feed to `feeds.json`, a schedule to
`schedule.json`) where there's no real code to get wrong. It's wrong for code.

## The core split

Deciding *what* to change and *making* the change are different competencies:

| Job | Best tool | Why |
|---|---|---|
| Decide what to change, articulate it | **Argo** | It's a judgment/articulation task — Argo's strength |
| Read, edit, test, iterate, PR | **Coding agent (Claude Code)** | Built for it: reads files, runs tests, respects conventions |

Argo writing file contents is "generate a diff and hope." A coding agent is
"read, change, verify." The split plays each to its strength.

## The three options (and tradeoffs)

**A — Status quo (Argo writes PRs via token).** Simplest, already built, no new
trust surface. But worst code quality and the flailing above. Keep ONLY for
trivial data edits.

**B — Argo dispatches an enriched spec to the coder (autonomous).** Best quality
(coder executes), Argo plays to its strength. Cost: a real integration + latency
(coding runs take minutes) + a new trust boundary (a chat bot can now trigger
repo edits).

**C — Argo proposes -> human approves -> coder executes (gated).** Safest: a human
gate between intent and code, reusing the existing CONFIRM pattern. Best quality
AND control. Cost: not fully autonomous; still needs the dispatch integration.

**Recommendation:** C to start (gated), with A retained for trivial data edits.
Graduate toward B only once the pipeline earns trust.

## The architecture

```
Yiya (Telegram) ──► Argo ──► [enrichment layer] ──► dispatch ──► Coding agent
                     │              │                   │              │
              decides WHAT     rough intent         human gate     reads/edits/
              needs changing   -> precise spec      (CONFIRM)      tests/opens PR
                                                                        │
Yiya reviews the PR ◄───────────────────────────────────────────────────┘
```

1. **Argo decides** a change is worth making (a fix, a new capability).
2. **Enrichment layer** turns Argo's rough "fix the pitch template" into a precise
   spec a coding agent can nail first try (see below). This is the product.
3. **Human gate** — Argo presents the spec; Yiya replies CONFIRM (reuse the
   existing gate; never auto-dispatch a code change).
4. **Dispatch** to the coding agent (mechanism TBD: API, queue, or a job).
5. **Coding agent** reads the real repo, makes the change, runs checks, opens a PR.
6. **Yiya reviews and merges.** Argo never merges.

## The enrichment layer (the actual product)

The gap between "fix the pitch" and a spec a coding agent executes cleanly is
enormous, and closing it is the high-leverage part. A good enriched spec carries:

- **Where:** the file(s) and the specific symbol/section (`src/argo_project.py`,
  the `PROPOSAL_INSTRUCTIONS` string), not "the pitch code somewhere."
- **What + why:** the change and its intent, so the coder makes the *right* edit,
  not a literal one.
- **Constraints:** invariants to preserve (the labeled-block format; plain-text
  voice; no new deps per CLAUDE.md).
- **Done check:** how to verify (`py_compile`; run the affected script; a test).

This is the SAME muscle as Argo's project proposals: turn a vague signal into a
precise, grounded bet. So it's in character for the system — arguably it's the
**Argo (build partner) engine** doing what it's for, and the spec-quality bar is
where **Rehearse** (stress the spec before dispatch) could later plug in.

## Trust & security (do not hand-wave this)

A Telegram bot that can dispatch repo edits is a real attack surface. Non-negotiables:

- **Human gate by default** (Option C). No code change dispatches without CONFIRM.
- **Coder PRs, never pushes to main** — same "propose-only, human merges" rule
  Argo already follows.
- **Scope the dispatch** — the coder works only in the approved repo, same
  allowlist discipline as the rest of the system.
- **Keep the data-edit path separate** — trivial `feeds.json` edits stay on the
  simple gated path; only code-shaped changes go to the coder.

## Open questions (to resolve before building)

- **Dispatch mechanism:** how does Argo reach Claude Code? (Headless invocation,
  an API/SDK, a job queue?) This is the main unknown.
- **Latency UX:** coding runs take minutes; how does Argo report progress over
  Telegram (reuse the heartbeat? the PR link when done)?
- **Where the enrichment runs:** in Argo's model call, or a dedicated step.
- **Failure handling:** what happens when the coder can't complete the change.

## Status

Design only. Nothing built. Recommended path: Option C (gated), enrichment layer
as the core value, data edits stay on the existing path. Validate the dispatch
mechanism question before committing to a build.
