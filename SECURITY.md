# Security Policy

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue or PR.

- Preferred: open a private vulnerability report via GitHub
  (repository **Security** tab → **Report a vulnerability**).
- Alternatively, open a regular issue that simply asks for a secure contact
  channel — do **not** include exploit details in a public issue.

Please include: what you found, how to reproduce it, and the impact you expect.
We aim to acknowledge within a few days. There is no bug-bounty program; this is
a research/personal project, but reports are genuinely appreciated and credited.

## What's in scope

The interesting surface is the Argo runtime (the live Telegram bot + its MCP
server) and the SEAS pipeline's network access:

- the bearer-authenticated `/mcp` endpoint;
- the web/repo fetch tools and their allowlist;
- the self-heal and self-create (PR) paths;
- secret handling across env / `.env` / GitHub Actions / Railway.

## Design posture (defense in depth)

These are deliberate, and worth knowing before you probe:

- **Server-side allowlist, never model-enforced.** Argo's own web and repo reads
  are gated against an approved host allowlist *in our code* — the model is never
  trusted to self-limit. This doubles as SSRF defense (no internal IPs / cloud
  metadata endpoints). `study_url` can read an off-allowlist page **only** when
  the human explicitly sends that URL, and its content is treated as untrusted
  data, never as instructions.
- **Firecrawl stays inside the boundary.** We call Firecrawl's REST API from a
  thin stdlib client (`firecrawl_client.py`) and re-impose our host allowlist on
  every result, rather than adopting the official `npx` Firecrawl MCP server —
  which would run outside our allowlist and add a Node runtime. Firecrawl
  augments source-gathering; it does not replace the security boundary.
- **Bearer auth on `/mcp`.** The MCP endpoint requires a token
  (`ARGO_MCP_TOKEN`); requests without it are rejected by the ASGI wrapper.
- **Separated, least-privilege tokens.** The self-create PR path uses a
  **PR-only** token (`ARGO_PROPOSE_TOKEN`), never the serving token, and is
  branch-only — Argo opens a pull request and **never self-merges or self-deploys**.
  The propose token is deliberately *not* granted GitHub Workflows scope (a merged
  workflow would run with all secrets — an exfiltration risk).
- **Gated self-heal.** Self-heal defaults to `ARGO_HEAL_LEVEL=L0` (report-only).
  At `L1` it proposes a fix and executes **only** after an explicit human
  "CONFIRM" reply in chat.
- **Secrets from env, stripped, never committed.** Keys/tokens come from the
  environment (or a local `.env`) and are `.strip()`-ed (a stray newline in a
  secret produces an illegal-header "Connection error"). `.env`, the active
  `data/profile.json`, and all runtime state files are gitignored.

## If you operate your own instance

- Set your **own** `ARGO_PROPOSE_REPO` (the in-repo default is a non-real
  placeholder, `your-org/your-repo`, specifically so a fork cannot open PRs
  against the upstream repository).
- Rotate any key that may have been exposed, and revoke the old one — the system
  reads keys fresh from the environment, so rotation is just an env update + restart.
- Keep `ARGO_HEAL_LEVEL` at `L0` unless you understand the `L1` confirm path.
