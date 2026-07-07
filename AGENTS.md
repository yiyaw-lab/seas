concise, to the point, and grounded in truths

SEAS senior-pair intake:
- Before starting non-trivial SEAS work, check the active branch, dirty tree, recent commits, and whether the request belongs in this chat.
- If the request is unrelated to the current branch or would mix Argo/SEAS lanes, recommend a fresh worktree from `origin/main` and a new chat before editing.
- Keep local work local unless the task needs Railway, Telegram, deployed webhook behavior, secrets, or production logs.
- At closeout, when useful, name the next 3-10 highest-leverage follow-ups and any workspace hygiene cleanup.
- For commits from a dirty or shared tree, use `commit-mine`; stage explicit paths only.

## Cursor Cloud specific instructions

Standard commands (tests, SEAS pipeline, running the webhook) are in `README.md`,
`CLAUDE.md`, and `CONTRIBUTING.md` — use those. Notes below are only the
non-obvious cloud-VM caveats.

- **Interpreter.** The repo pins Python 3.11 (`runtime.txt`), but the VM's system
  `python3` is 3.12. A Python 3.11 venv lives at `/workspace/.venv` (gitignored) —
  prefer it: `PYTHONPATH=src .venv/bin/python ...`. The update script keeps its
  deps fresh. Deps are also installed for system `python3` (3.12) so bare
  `python3` commands from the docs still run.
- **Test suite is fully green only on 3.11.** Under system 3.12, exactly one test
  fails — `test_seasar_substance.test_deeply_nested_json_does_not_raise` — because
  CPython 3.12's `json` no longer raises `RecursionError` on ~5000-deep nested
  arrays. It is a stdlib-version artifact, not a regression. Run the suite with
  `.venv/bin/python` (3.11) for 951/951.
- **Running Argo locally needs no secrets.** `PYTHONPATH=src PORT=8080
  .venv/bin/python src/argo_webhook.py` boots fine with no keys: webhook
  self-register no-ops (no `WEBHOOK_URL`/`TELEGRAM_BOT_TOKEN`), the in-process
  scheduler runs on a daemon thread, and the tripwire skips its LLM judge. Health
  is `GET /` (live JSON from local files). `POST /webhook` returns 200 but the
  chat reply cannot be delivered without `TELEGRAM_BOT_TOKEN`/`_CHAT_ID` (+ an LLM
  key) — expected, not a bug.
- **MCP endpoint path.** FastMCP's internal route (`/mcp`) is mounted under
  `/mcp`, so the real Streamable-HTTP endpoint is `/mcp/mcp`. It is bearer-gated by
  `ARGO_MCP_TOKEN` (the one secret present in this env); without that env var the
  mount returns 503, and hitting `/mcp` (no `/mcp` suffix) returns 404.
- **`ARGO_MCP_TOKEN` must reach the server process.** It is not always inherited by
  detached tmux panes; start the webhook from a shell/pane where the env var is
  set, or the `/mcp` mount serves 503.
- **Keys.** When LLM/Telegram/Firecrawl keys are absent, tests still run (they
  need none), SEAS emits `premature`/dry-run probes, and Argo chat can't deliver.
  The scheduler writes `data/*.json` and logs a benign "ARGO_SEEN_PATH is not
  pointed at a volume" warning locally.
- **Tool-augmented Argo chat needs a PUBLIC `WEBHOOK_URL`.** Anthropic's MCP
  connector (not the VM) connects to `<WEBHOOK_URL>/mcp/mcp`, so tools only wire
  up when `WEBHOOK_URL` is a public https URL — e.g. a `cloudflared tunnel --url
  http://localhost:8080` quick tunnel. Setting `WEBHOOK_URL` also self-registers
  the Telegram webhook on boot. Without it you get plain chat only ("tools wired:
  False"); outbound Telegram replies work regardless.
- **A real `src/seas.py` run mutates committed knowledge.** With an LLM key +
  `FIRECRAWL_API_KEY` it writes `data/world_model.json`, `data/probes.json`, and
  new `findings/F-*.json` + `runs/F-*/` (committed durable state, not gitignored).
  Revert these after ad-hoc/verification runs; committing findings is a product
  action (the `seas-findings.yml` workflow), not a dev-loop side effect.
