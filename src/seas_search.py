"""Topical sources for SEAS synthesis, inside the existing host allowlist.

An unconfigured installation uses anonymous Parallel Search MCP. A configured
Firecrawl key retains Firecrawl's search categories and scraped content. Explicit
SEAS_SEARCH_PROVIDER=parallel|firecrawl|none wins; failures never change providers.
Parallel returns excerpts, not full pages. No new service or model tool is exposed.
"""
import asyncio
import json
import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import argo_http
import firecrawl_client
from argo_log import get_logger

log = get_logger(__name__)
MCP_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "argo-seas/1.0"
DEFAULT_TIMEOUT = 25
MAX_CONTENT_CHARS = 6000


def source_key(url):
    """Do not count arXiv's HTML/PDF/version URLs as independent papers."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host == "arxiv.org" or host.endswith(".arxiv.org"):
            paper = re.fullmatch(
                r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})"
                r"(?:v\d+)?(?:\.pdf)?/?", parts.path)
            if paper:
                return "arxiv:" + paper[1].lower()
    except ValueError:
        pass
    return url.partition("#")[0]


def selected_provider():
    """Resolve explicit selection first, then existing credentials, then Parallel."""
    choice = os.environ.get("SEAS_SEARCH_PROVIDER", "").strip().lower()
    return choice or ("firecrawl" if firecrawl_client.is_enabled() else "parallel")


def _allowed(url, allowed_hosts):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme == "https" and parts.username is None and parts.password is None
                and parts.port in (None, 443)
                and any(host == h or host.endswith("." + h) for h in allowed_hosts))
    except ValueError:
        return False


def _sources(result, allowed_hosts, limit):
    """Accept only content-bearing HTTPS results from approved hosts."""
    if result.isError:
        raise ValueError("Parallel MCP web_search returned an error")
    data = json.loads("\n".join(c.text for c in result.content if c.type == "text"))
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Parallel MCP returned an invalid search result")
    out, seen = [], set()
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url, excerpts = item.get("url"), item.get("excerpts")
        if (not isinstance(url, str) or not _allowed(url, allowed_hosts)
                or source_key(url) in seen or not isinstance(excerpts, list)):
            continue
        text = "\n\n".join(e for e in excerpts if isinstance(e, str) and e.strip())
        if not text:
            continue
        title = item.get("title")
        out.append({"url": url, "title": title if isinstance(title, str) else "",
                    "text": text[:MAX_CONTENT_CHARS]})
        seen.add(source_key(url))
        if len(out) >= limit:
            break
    return out


async def _parallel_search(query, allowed_hosts, limit):
    # Reuse the installed MCP SDK. Keep these imports lazy for disabled search
    # and the existing Firecrawl path. One investigation is one search session.
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with asyncio.timeout(DEFAULT_TIMEOUT):
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT,
            verify=argo_http.tls_context(),
        ) as client:
            async with streamable_http_client(MCP_URL, http_client=client) as streams:
                async with ClientSession(
                    streams[0], streams[1],
                    read_timeout_seconds=timedelta(seconds=DEFAULT_TIMEOUT),
                ) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if not any(t.name == "web_search" for t in tools.tools):
                        raise ValueError("Parallel MCP web_search is unavailable")
                    result = await session.call_tool("web_search", {
                        "objective": "Find independent corroborating sources about: " + query,
                        "search_queries": [query], "session_id": uuid.uuid4().hex,
                    })
                    return _sources(result, allowed_hosts, limit)


def search_related(query, category=None, limit=5, allowed_hosts=()):
    """Return {url,title,text} sources, [] when disabled, or None on failure.

    Called by the synchronous SEAS pipeline. Parallel's total discovery/search
    budget is 25s; cancellation propagates through its async transport. It has no
    Firecrawl category equivalent; choose Firecrawl to retain category filtering.
    An empty allowlist never sends a topical search to either provider.
    """
    provider = selected_provider()
    if provider == "none" or not query.strip() or limit <= 0 or not allowed_hosts:
        return []
    if provider == "firecrawl":
        return firecrawl_client.search_related(query, category=category, limit=limit,
                                              allowed_hosts=allowed_hosts)
    if provider != "parallel":
        log.warning("Unknown SEAS_SEARCH_PROVIDER; topical search skipped")
        return None
    try:
        return asyncio.run(_parallel_search(query[:500], allowed_hosts, min(limit, 5)))
    except Exception:  # outer pipeline boundary: a failed search must not abort synthesis
        log.warning("Parallel topical search unavailable", exc_info=True)
        return None
