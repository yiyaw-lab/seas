"""Grok live-search frontier signals — optional X/web tripwire source.

xAI's Agent Tools API (POST /v1/responses with the server-side web_search and
x_search tools) lets Grok pull CURRENT AI-frontier launches/announcements from X
and the open web -- the breaking signal RSS feeds structurally miss (most launches
break on X first). Returns candidate items in argo_watch's {title, link, summary}
shape, so the watch judge applies Argo's bar and the seen-store dedups them like any
other source: this just widens the candidate pool.

OPTIONAL and OPT-IN. It is a paid, search-heavy call (~5 server-side searches per
run), so it stays OFF unless BOTH are set:
  - XAI_API_KEY        (the key)
  - ARGO_GROK_SOURCE=1 (the cost switch; anything else = off)
With it off, fetch() returns [] and the tripwire runs on RSS alone. Every failure
also degrades to [] -- an additive source must never break the RSS tripwire.

Reuses argo_http.tls_context (certifi TLS) like the other outbound calls; no new dep
(stdlib urllib + the existing certifi).
"""

import json
import os
import urllib.error
import urllib.request

import argo_http
from argo_log import get_logger

log = get_logger(__name__)

API_URL = "https://api.x.ai/v1/responses"
DEFAULT_TIMEOUT = 120          # web+X search + reasoning is slow; batch job, so fine
MAX_ITEMS = 6                  # cap candidates handed to the judge
SUMMARY_CHARS = 300

_PROMPT = (
    "Use web and X search. Return ONLY a JSON array (no prose, no markdown fence) "
    "of up to 6 AI-frontier items from the last 48 hours -- new model/product "
    "launches, major lab announcements (OpenAI, Anthropic, Google DeepMind, Meta, "
    "xAI, Mistral, DeepSeek), or tools builders will adopt widely. Each item: "
    '{"title": ..., "url": ..., "summary": ...} with a REAL, working source url. '
    "Prefer primary sources. If nothing qualifies, return []."
)


def is_enabled():
    """True only when a key is present AND the cost switch is on. Callers check this
    before fetch() so a no-key / switch-off deploy never makes the paid call."""
    return bool(os.environ.get("XAI_API_KEY")) and \
        os.environ.get("ARGO_GROK_SOURCE", "0") == "1"


def _post(body, timeout):
    """POST to the xAI Responses API; return parsed JSON or None. Never raises."""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + key.strip(),
            "Content-Type": "application/json",
            "User-Agent": "argo-seas/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=argo_http.tls_context()) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("grok search request failed: %s: %s", type(exc).__name__, exc)
        return None


def _extract_text(data):
    """Join the output_text blocks of a Responses-API result (the model's answer,
    separate from the server-side tool-call blocks)."""
    parts = []
    for item in (data or {}).get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return "\n".join(parts)


def _parse_items(text):
    """Pull a JSON array of {title,url,summary} out of the model's text, tolerant of
    a ```json fence or surrounding prose. Bad/empty -> []. Maps to watch's item
    shape (link, not url)."""
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(arr, list):
        return []
    items = []
    for o in arr:
        if not isinstance(o, dict):
            continue
        url = str(o.get("url") or "").strip()
        title = str(o.get("title") or "").strip()
        if url.startswith("http") and title:
            items.append({
                "title": title,
                "link": url,
                "summary": str(o.get("summary") or "")[:SUMMARY_CHARS],
            })
    return items[:MAX_ITEMS]


def fetch(timeout=DEFAULT_TIMEOUT):
    """Return live Grok-sourced frontier candidates as argo_watch items, or [] when
    disabled or on any failure (the RSS tripwire must keep running regardless)."""
    if not is_enabled():
        return []
    data = _post({
        "model": os.environ.get("XAI_SEARCH_MODEL") or "grok-4.3",
        "input": [{"role": "user", "content": _PROMPT}],
        "tools": [{"type": "web_search"}, {"type": "x_search"}],
    }, timeout)
    items = _parse_items(_extract_text(data))
    log.info("grok search: %d candidate item(s)", len(items))
    return items
