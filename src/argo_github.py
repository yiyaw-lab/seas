"""GitHub read access for Argo, extracted from argo_mcp_server.

The repo-read tools (github_read_file, github_list) had a self-contained stack
buried in the 1200-line MCP server: an allowlist check, a short-timeout API call
with one transient retry, and the read/list bodies. That stack moves here as plain
functions; the @mcp.tool() wrappers stay in argo_mcp_server (they register on the
FastMCP instance at import, so they can't move) and delegate to these.

The retry budget (GH_CALL_TIMEOUT) and the retry predicate (gh_retryable) are
shared with the PROPOSE write path, which still lives in argo_mcp_server and
imports them from here -- so the two GitHub stacks agree on what 'transient'
means. Stdlib + argo_http for TLS.
"""

import os
import urllib.request

import argo_http

# Repos Argo may read. Comma-separated owner/repo in GITHUB_REPO_ALLOWLIST; "*"
# (the default) allows ANY repo so Argo can read trending/other repos it surfaces.
# Reads are read-only and size-capped (same risk class as web_fetch). Public
# repos need no token; private repos require GITHUB_TOKEN. Set the env var to a
# specific list if you ever want to restrict it.
def repo_allowlist():
    raw = os.environ.get("GITHUB_REPO_ALLOWLIST", "*")
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def repo_allowed(repo):
    allow = repo_allowlist()
    return "*" in allow or repo.lower() in allow


# Per-GitHub-call timeout (down from 20-25s). Kept short so one slow call fails
# fast and retries once, instead of consuming a multi-call tool's whole deadline.
GH_CALL_TIMEOUT = 10


def gh_retryable(exc):
    """A transient stall worth one retry: timeouts and 5xx. NOT 4xx (a 404/403
    will just repeat) — retrying those wastes the deadline."""
    s = str(exc)
    if isinstance(exc, TimeoutError) or "timed out" in s.lower():
        return True
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return 500 <= code < 600
    return any(x in s for x in ("500", "502", "503", "504"))


def gh_api(path, raw=False):
    """Call the GitHub REST API; return (ok, text). Uses GITHUB_TOKEN if set."""
    ctx = argo_http.tls_context()

    headers = {
        "User-Agent": "argo-mcp/1.0",
        "Accept": "application/vnd.github.raw+json" if raw
                  else "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token.strip()

    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    # Short per-call timeout + one retry on a transient stall: a single slow call
    # must not eat the tool's whole deadline. 404 etc. are not retried (the retry
    # only helps timeouts/5xx; a 404 will just 404 again).
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=GH_CALL_TIMEOUT, context=ctx) as r:
                return True, r.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last = exc
            if not gh_retryable(exc):
                break
    hint = ""
    if "404" in str(last):
        hint = " (private repo? needs GITHUB_TOKEN, or wrong path)"
    return False, f"GitHub API error: {type(last).__name__}: {last}{hint}"


def gh_read_file(repo, path, max_chars, offset=0, limit=0):
    """Read a file from a GitHub repo (allowlist-gated, size-capped). Returns the
    file text or a refusal/error string. Backs the github_read_file MCP tool.

    With offset (1-based start line) and/or limit (line count), return just that
    window instead of the whole file -- so a large file can be inspected a span at a
    time. The result is still capped at max_chars."""
    if "/" not in repo:
        return "Refused: repo must be 'owner/name'."
    if not repo_allowed(repo):
        return (f"Refused: '{repo}' is not on Argo's approved repo list "
                f"({', '.join(sorted(repo_allowlist()))}).")
    ok, body = gh_api(f"/repos/{repo}/contents/{path.lstrip('/')}", raw=True)
    if not ok:
        return body
    if offset or limit:
        lines = body.splitlines()
        start = max(0, (offset or 1) - 1)
        body = "\n".join(lines[start:start + limit] if limit else lines[start:])
    return body[:max_chars] or "(empty file)"


def gh_list(repo, path=""):
    """List files/dirs in a GitHub repo at `path` (allowlist-gated). Returns a
    text listing or a refusal/error string. Backs the github_list MCP tool."""
    import json

    if "/" not in repo:
        return "Refused: repo must be 'owner/name'."
    if not repo_allowed(repo):
        return (f"Refused: '{repo}' is not on Argo's approved repo list "
                f"({', '.join(sorted(repo_allowlist()))}).")
    ok, body = gh_api(f"/repos/{repo}/contents/{path.lstrip('/')}")
    if not ok:
        return body
    try:
        entries = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return "(could not parse listing)"
    if isinstance(entries, dict):  # a file path, not a dir
        return f"{entries.get('name')} (file, {entries.get('size')} bytes)"
    return "\n".join(f"{e['type']:4s}  {e['name']}" for e in entries) or "(empty)"
