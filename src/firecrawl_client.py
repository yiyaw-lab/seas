"""
Firecrawl — optional web search + scrape, behind our own abstractions.

WHY this exists: SEAS V3's synthesis floor needs to find sources TOPICALLY
related to a signal (to ground a finding in cross-source convergence). Our
stdlib web_fetch can only read a URL we already have; it can't search, and it
chokes on JS/403 pages. Firecrawl's /search returns related sources WITH their
content in one call, and absorbs the scraping mess server-side.

DESIGN CONSTRAINTS (this is why it's a thin urllib wrapper, not the SDK):
  - OPTIONAL. No FIRECRAWL_API_KEY -> every function returns None and callers
    fall back to stdlib. The system never hard-depends on a paid API to operate.
  - stdlib-first. We call the REST API with urllib (already used everywhere),
    so there is NO new pip dependency — just an optional key.
  - The allowlist still rules. Firecrawl can fetch anything; our security model
    is server-side host allowlisting. So search results are FILTERED against the
    caller-supplied allowlist before we return them. Firecrawl sits INSIDE our
    security boundary, it does not replace it.

Used by seas_finding (related search) and, as a fallback, by web_fetch (when a
direct urllib fetch fails on a 403/JS page).
"""

import json
import os
import urllib.request
from urllib.parse import urlparse

import argo_http

API_BASE = "https://api.firecrawl.dev/v2"
DEFAULT_TIMEOUT = 25  # server-side scraping is slower than a raw fetch
MAX_CONTENT_CHARS = 6000  # match web_fetch's cap; this is a scout, not a scraper


def is_enabled():
    """True if a Firecrawl key is configured. Callers check this to decide
    whether to use Firecrawl or fall back to stdlib."""
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


def _ctx():
    return argo_http.tls_context()


def _post(path, body, timeout=DEFAULT_TIMEOUT):
    """POST to a Firecrawl endpoint. Returns (ok, parsed_or_error). Never raises
    — a Firecrawl failure must degrade to fallback, not crash the caller."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return False, "FIRECRAWL_API_KEY not set"
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + key.strip(),
            "Content-Type": "application/json",
            "User-Agent": "argo-seas/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            return True, json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _host_ok(url, allowed_hosts):
    """Re-impose the host allowlist on a Firecrawl result. If allowed_hosts is
    None, allow all (caller opted out); else require an exact/subdomain match —
    the same rule argo_mcp_server._host_allowed uses."""
    if allowed_hosts is None:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in allowed_hosts)


def search_related(query, category=None, limit=5, allowed_hosts=None):
    """Search the web for sources related to `query`, returning their content.

    This is the clustering keystone: given a signal's topic, find OTHER sources
    about it so the synthesis floor can look for cross-source convergence.

    Returns a list of {"url","title","text"} (content-bearing results only,
    filtered through the host allowlist), or None if Firecrawl is unavailable
    (so the caller falls back). `category` is 'research'|'github'|'pdf' or None.
    """
    if not is_enabled():
        return None
    body = {
        "query": query[:500],
        "limit": limit,
        "scrapeOptions": {"formats": [{"type": "markdown"}], "onlyMainContent": True},
    }
    if category:
        body["categories"] = [{"type": category}]
    ok, res = _post("/search", body, timeout=DEFAULT_TIMEOUT)
    if not ok or not isinstance(res, dict) or not res.get("success"):
        return None
    out = []
    for item in (res.get("data", {}) or {}).get("web", []) or []:
        url = item.get("url", "")
        md = item.get("markdown") or ""
        if not url or not md:
            continue
        if not _host_ok(url, allowed_hosts):
            continue
        out.append({
            "url": url,
            "title": item.get("title", ""),
            "text": md[:MAX_CONTENT_CHARS],
        })
    return out


def scrape(url, timeout=DEFAULT_TIMEOUT):
    """Scrape one URL to clean markdown (the JS/403-resilient fallback for
    web_fetch). Returns the text, or None if unavailable/failed so the caller
    keeps its own error path. The CALLER is responsible for allowlist-gating the
    url before calling this (same as web_fetch) — scrape does not re-check, since
    it's only ever called on an already-approved url."""
    if not is_enabled():
        return None
    ok, res = _post("/scrape",
                    {"url": url,
                     "formats": [{"type": "markdown"}], "onlyMainContent": True},
                    timeout=timeout)
    if not ok or not isinstance(res, dict) or not res.get("success"):
        return None
    md = (res.get("data", {}) or {}).get("markdown") or ""
    return md[:MAX_CONTENT_CHARS] or None
