# SEAS source search (Parallel and optional Firecrawl)

SEAS's emission gate needs **cross-source convergence**: a finding is only earned
when ≥2 *independent* sources on the same topic corroborate a claim. Finding those
related sources requires web *search*, not just fetching a URL you already have.
An unconfigured installation uses [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
at `https://search.parallel.ai/mcp`. Search uses Fast mode and requires no account
or API key. It returns relevant excerpts, which SEAS passes to synthesis with
their source URLs. The existing emission gate still checks every cited quote.

## Do you need it?

| You have… | SEAS behavior |
|---|---|
| no search configuration | Parallel adds topical excerpts from approved hosts. |
| `FIRECRAWL_API_KEY` set | Retains Firecrawl search categories and scraped page content. |
| `SEAS_SEARCH_PROVIDER=parallel` | Explicitly selects anonymous Parallel, even with a Firecrawl key. |
| `SEAS_SEARCH_PROVIDER=firecrawl` | Explicitly selects Firecrawl; requires `FIRECRAWL_API_KEY`. |
| `SEAS_SEARCH_PROVIDER=none` | Disables topical search; the signal's own source is still fetched. |

Set these variables in your environment or `.env`, then use the usual
`PYTHONPATH=src python3 src/seas.py` pipeline. There is no extra service to run;
the existing Python MCP dependency supplies the Streamable HTTP client.

Parallel results are filtered against Argo's approved-host allowlist before they
reach the model. Search returns at most five related sources, each capped at
6,000 characters. Parallel excerpts are not full-page scrapes and do not support
Firecrawl's research/GitHub/PDF categories; select Firecrawl when you need them.
Alternate arXiv formats and versions count as one paper when gathering sources.

Free anonymous access has service limits. A failed or rate-limited search keeps
the original source and may yield an honest `premature` probe. Discovery and
search share a 25-second deadline. Errors never switch to another provider.
Unknown provider values skip topical search with an operator warning.

## Firecrawl setup

1. Get a key at <https://firecrawl.dev> (`fc-...`).
2. Add it to your environment / `.env`:
   ```
   FIRECRAWL_API_KEY=fc-...
   ```
3. With `SEAS_SEARCH_PROVIDER` unset, the pipeline detects the key and retains
   `/v2/search` for topical source-gathering automatically. Explicit selections
   take precedence over credentials.

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
