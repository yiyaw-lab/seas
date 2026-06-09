# Firecrawl setup (optional, recommended for SEAS)

SEAS's emission gate needs **cross-source convergence**: a finding is only earned
when ≥2 *independent* sources on the same topic corroborate a claim. Finding those
related sources requires web *search*, not just fetching a URL you already have.
That's what Firecrawl provides.

## Do you need it?

| You have… | SEAS behavior |
|---|---|
| no `FIRECRAWL_API_KEY` | Each investigation sees only the signal's own source → can't reach 2 sources → honest `premature` probe. The pipeline runs, but rarely emits findings. |
| `FIRECRAWL_API_KEY` set | `_gather_sources` adds topical related sources → the gate can test convergence → real findings get emitted. |

So Firecrawl is **optional but effectively required to demonstrate the finding
path.** Without it you'll mostly see probes (which is the system being honest, not
broken).

## Setup

1. Get a key at <https://firecrawl.dev> (`fc-...`).
2. Add it to your environment / `.env`:
   ```
   FIRECRAWL_API_KEY=fc-...
   ```
3. That's it. `src/firecrawl_client.py` detects the key (`is_enabled()`) and the
   pipeline starts using `/v2/search` for topical source-gathering automatically.

## Why a thin stdlib client, not the official MCP server

Firecrawl ships an official `npx firecrawl-mcp` server. We deliberately **do not**
use it. Instead `firecrawl_client.py` is a ~130-line `urllib` wrapper over the v2
REST API (`/v2/search`, `/v2/scrape`, Bearer auth). Reasons:

- **Security boundary.** Argo's whole posture is a *server-side host allowlist* we
  enforce ourselves. The client re-imposes that allowlist on every Firecrawl
  result (`_host_ok`), so search results are filtered to approved hosts before
  they reach the model. The official MCP server runs outside that boundary.
- **No new runtime.** The MCP server is a Node process; this repo is
  stdlib-first Python. A `urllib` call adds an optional *key*, not a dependency.
- **Graceful degradation.** Every function returns `None` when the key is absent,
  so callers fall back to stdlib fetch and the system never hard-depends on a paid
  API to operate.

See [SECURITY.md](../SECURITY.md) for the full posture.
