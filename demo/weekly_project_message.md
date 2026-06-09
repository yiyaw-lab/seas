# Argo Weekly Project — Sample Output (V2 format)

> The real message Argo sends to Telegram each Friday. Plain text, no markdown.
> Generated from frontier signals via `argo_project.py`; rated 1-10 by the user
> to teach Argo their taste; stress-tested via REHEARSE before locking in.

---

⚓ Argo

I've been watching something.

Tool-calling models are getting fast enough that the scaffolding around them, not the model itself, is now the bottleneck. The MCP spec ships new transport options faster than most integrations can absorb them.

That gap is the opening: the teams moving quickest are the ones who treat the protocol as data, not code.

This week's bet:
MCP Diff Tracker

Build a lightweight tool that watches the MCP spec changelog and surfaces breaking changes as structured diffs. When a new transport or capability drops, it flags which integrations in a given codebase need updating and why.

Artifact:
A CLI tool + GitHub Action that runs on a repo and outputs a plain-English change report.

Effort:
A weekend

Potential upside:
Every team shipping MCP integrations needs this and nobody has built the obvious version yet. If the MCP ecosystem grows the way the OpenAPI ecosystem did, this becomes a daily-use dev tool.

---

*Reply 1-10 to rate how much you want to build it. REHEARSE to stress-test it
with three critics and a judge before committing. SELECT to lock it in and get
a kickoff plan.*
