# Build Log

What got built, why, and what's next — one file per session, dated. Newest first.

- [2026-06-11](2026-06-11.md) — both loops close, across three parallel sessions.
  The self-improvement loop (PR #10) goes live: incidents → diagnose → human FIX →
  verified PR → post-deploy confirmation. The **frontier-evolution loop** (PR #11)
  is built: watch release feeds → map against an honest stack manifest → one
  EVOLVE/SKIP nudge a day → rehearse-gated upgrade PRs → **dated predictions armed
  at merge and scored against the incident ledger**. Plus: the scheduler placement
  gap found and fixed (the webhook now runs volume-bound commands in-process),
  Telegram file reading, and capability gaps that become proposed PRs instead of
  shrugs.
- [2026-06-09](2026-06-09.md) — the repo goes public (perception review, history
  audit, public README/docs, first committed V3 evidence) — and the cleanup itself
  breaks the bot: gitignoring the runtime stores severed Actions commit-back
  persistence, which *was* the long-standing duplicate-news bug. Root-caused and
  fixed same day, with phantom-send honesty (deterministic proposal route, tool
  telemetry) and the reply-context fix finally wired in.
- [2026-06-08](2026-06-08.md) — Argo drafts its own fixes: two live chat bugs
  (lost reply context, "project" false-triggering) become two Argo-authored PRs
  (#6–#7), human-reviewed and merged the same evening. Honest postscript: one fix
  was written but never wired in — the lesson that later became a mechanical gate.
- [2026-06-06](2026-06-06.md) — hardening the factory + the keystone. **Rehearse**:
  a debate gate (3 adversaries + an Opus judge) that stress-tests a SELECTed bet
  into a build-ready blueprint before the build. Plus a user-profile abstraction
  (de-hardcode the single user), a strength-gated tripwire, a 39-test harness +
  structured logging, and a shared-utils refactor (store/http/paths/rating/github).
  Ran across parallel agent sessions; CLAUDE.md gained collaboration rules.
- [2026-06-05](2026-06-05.md) — SEAS V3, the self-correcting reasoning spine: a
  gated Finding stage (cite real evidence + a falsifiable prediction or be
  rejected), a world model with evidence-only confidence, Argo learning from
  screenshots/URLs, and a cross-provider model benchmark.
- [2026-06-04 (session 2: Phase E)](2026-06-04-phase-e.md) — the closed loop:
  self-heal (gated) + self-create (propose-only). Argo verified a feed, drafted
  PR #1 itself, and we merged it — the agent extended its own capabilities
  through the safe gate. Plus cost-control fixes (Sonnet default), CLAUDE.md, and
  schedules/feeds-as-data.
- [2026-06-04 (session 1)](2026-06-04.md) — Argo becomes agentic: two-way Telegram chat
  (Claude + routing), persistent memory, live web fetch (MCP, allowlist), and the
  tripwire watcher (proactive alerts). Plus the decision-engine → insight-engine
  redefinition.
