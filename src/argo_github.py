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
import shutil
import subprocess
import urllib.request
from pathlib import Path

import argo_http
import argo_paths

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


def _cap_bytes(text, max_bytes):
    """Truncate to at most max_bytes of UTF-8 (not characters), dropping a partial
    trailing multibyte char. Byte-based so the cap matches MAX_PROPOSE_BYTES, which is
    also a byte limit -- otherwise a non-ASCII full read could exceed the propose cap."""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", errors="ignore")


def gh_read_file(repo, path, max_bytes, offset=0, limit=0, ref=None):
    """Read a file from a GitHub repo (allowlist-gated, size-capped). Returns the
    file text or a refusal/error string. Backs the github_read_file MCP tool.

    With offset (1-based start line) and/or limit (line count), return just that
    window instead of the whole file -- so a large file can be inspected a span at a
    time. The result is capped at max_bytes of UTF-8. `ref` pins a branch/sha (so a read
    can match the branch a later edit is applied against); None reads the default branch."""
    if "/" not in repo:
        return "Refused: repo must be 'owner/name'."
    if not repo_allowed(repo):
        return (f"Refused: '{repo}' is not on Argo's approved repo list "
                f"({', '.join(sorted(repo_allowlist()))}).")
    api_path = f"/repos/{repo}/contents/{path.lstrip('/')}"
    if ref:
        api_path += f"?ref={ref}"
    ok, body = gh_api(api_path, raw=True)
    if not ok:
        return body
    if offset or limit:
        lines = body.splitlines()
        start = max(0, (offset or 1) - 1)
        window_lines = lines[start:start + limit] if limit > 0 else lines[start:]
        # Empty SLICE = out of range; an empty JOIN (e.g. a selected blank line) is a
        # real, empty snippet -- test the slice, not the joined text.
        if not window_lines:
            return f"(no lines in that range; file has {len(lines)} lines)"
        return _cap_bytes("\n".join(window_lines), max_bytes)
    return _cap_bytes(body, max_bytes) or "(empty file)"


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


# --- Local self-comprehension reads -----------------------------------------
# search_self and the diagnose code-context read the LOCAL checkout (the bytes Argo
# is actually running), unlike the GitHub-API tools above which read merged main. A
# local-filesystem read is a new, broader surface, so it is confined SERVER-SIDE --
# the model is never trusted to "only read the repo". Reads are restricted to source/
# test/doc files UNDER the repo root; secrets and the data volume (chat logs, the
# incident ledger with raw exception bodies, tokens) are hard-excluded. A path is the
# only model-supplied input and it goes through _confined_path before any read.
MAX_LOCAL_READ_BYTES = 40_000          # a window/snippet, not a whole 90KB module
_SELFREAD_ROOT = argo_paths.ROOT.resolve()
_SELFREAD_EXTS = (".py", ".md", ".toml", ".cfg", ".yml", ".yaml")  # NOT .json/.env/logs
_SELFREAD_DENY_DIRS = ("data", ".git", ".github")  # data volume, git internals, CI secrets
_SELFREAD_DENY_NAMES = (".env",)       # belt-and-suspenders (also fails the ext allowlist)
_SEARCH_DIRS = ("src", "tests", "docs")  # where Argo's code/tests/build-log live


def _confined_path(path):
    """Resolve `path` against the repo root and return the safe absolute Path, or None
    if it escapes the root (../, absolute, symlink-out), hits a denied dir/name, or is
    not an allowed source extension. The SINGLE chokepoint for every local self-read --
    read_local_source and code_search's per-hit reads both go through here."""
    try:
        candidate = (_SELFREAD_ROOT / str(path)).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    # .resolve() collapses ../ and follows symlinks, so a symlink pointing outside the
    # repo resolves to an outside path and fails this containment check.
    if not candidate.is_relative_to(_SELFREAD_ROOT):
        return None
    parts = candidate.relative_to(_SELFREAD_ROOT).parts
    if not parts:
        return None
    if parts[0] in _SELFREAD_DENY_DIRS or parts[0] in _SELFREAD_DENY_NAMES:
        return None
    if candidate.suffix not in _SELFREAD_EXTS:
        return None
    return candidate


def read_local_source(path, offset=0, limit=0, max_bytes=MAX_LOCAL_READ_BYTES):
    """Read a source/test/doc file from the LOCAL checkout, confined to the repo's
    source tree (never .env, data/, .git, or CI). Optional 1-based line window via
    offset/limit. Capped at max_bytes; returns the text or a refusal string. The one
    safe local reader -- callers must route every local read through this."""
    safe = _confined_path(path)
    if safe is None:
        return f"Refused: '{path}' is not a readable repo source path."
    try:
        text = safe.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        return f"Could not read '{path}': {type(exc).__name__}"
    if offset or limit:
        lines = text.splitlines()
        start = max(0, (offset or 1) - 1)
        window = lines[start:start + limit] if limit > 0 else lines[start:]
        if not window:
            return f"(no lines in that range; file has {len(lines)} lines)"
        text = "\n".join(window)
    return _cap_bytes(text, max_bytes) or "(empty file)"


def _search_roots():
    """The existing source dirs to search (never data/.git/.github). Bounds the walk."""
    return [d for d in _SEARCH_DIRS if (_SELFREAD_ROOT / d).is_dir()]


_MAX_SEARCH_BYTES = 1_000_000  # per-file read cap, shared by BOTH search paths so the
                               # rg and stdlib results can't diverge on big files


def _code_search_fallback(pattern, max_results):
    """Stdlib substring search (literal, == ripgrep -F) for hosts without rg -- the
    likely live case (a minimal Railway image). Walks only the source dirs, and routes
    every candidate through _confined_path so a symlink pointing out of the tree (or a
    non-source file) is NEVER read -- identical confinement to read_local_source. Skips
    files over _MAX_SEARCH_BYTES, matching rg's --max-filesize, so neither path reads a
    huge file (which would also blow the 10s tool deadline)."""
    hits = []
    for root in _search_roots():
        for p in sorted((_SELFREAD_ROOT / root).rglob("*")):
            if len(hits) >= max_results:
                return hits
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(_SELFREAD_ROOT)
            if _confined_path(str(rel)) is None:  # extension allowlist + containment
                continue
            try:
                if p.stat().st_size > _MAX_SEARCH_BYTES:  # match rg --max-filesize
                    continue
                for i, line in enumerate(
                        p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern in line:
                        hits.append(f"{rel}:{i}:{line.strip()[:300]}")
                        if len(hits) >= max_results:
                            return hits
            except (OSError, ValueError):
                continue
    return hits


def code_search(pattern, max_results=40):
    """Search Argo's OWN source tree for a literal `pattern`, returning up to
    max_results 'path:line:text' matches. Lets Argo enumerate its own code (every
    @with_deadline, where opened_at is read, how many tools exist) instead of guessing
    from a partial read. Fixed-strings over a FIXED root (the repo's src/tests/docs),
    secrets/data excluded -- the pattern is the only model input and is never a shell
    arg or a regex. ripgrep if present, else a bounded stdlib walk."""
    pattern = (pattern or "").strip()
    if not pattern:
        return "Refused: empty search pattern."
    try:
        max_results = max(1, min(int(max_results), 200))
    except (TypeError, ValueError):
        max_results = 40
    roots = _search_roots()
    if not roots:
        return "(no searchable source dirs)"
    rg = shutil.which("rg")
    if rg:
        # No per-file --max-count: enumeration (every @with_deadline) is the point, so
        # bound the TOTAL by max_results below, not per file. --max-columns guards a
        # single pathological long line; --max-filesize guards a huge blob. Restrict to
        # the SAME extension allowlist the fallback enforces via _confined_path, so the
        # two paths return identical results regardless of whether rg is installed.
        ext_globs = []
        for ext in _SELFREAD_EXTS:
            ext_globs += ["-g", "*" + ext]
        cmd = [rg, "--no-heading", "--line-number", "--color", "never",
               "--fixed-strings", "--max-filesize", str(_MAX_SEARCH_BYTES),
               "--max-columns", "300", *ext_globs, "--", pattern, *roots]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                  cwd=str(_SELFREAD_ROOT))  # never shell=True; no -L so no symlink follow
            # rg exit codes: 0 = matches, 1 = no matches (both fine), 2+ = a real error.
            # subprocess.run does NOT raise on non-zero, so without this check an rg error
            # would look like "no matches" and silently skip the stdlib fallback.
            if proc.returncode >= 2:
                raise OSError(f"rg exited {proc.returncode}: {proc.stderr[:120]}")
            raw = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            # Confine rg results too (defense in depth): drop any hit whose path is not a
            # confined source file, so a symlink-out can't leak even if rg surfaced it.
            hits = [h for h in raw
                    if _confined_path(h.split(":", 1)[0]) is not None][:max_results]
        except (subprocess.SubprocessError, OSError):
            hits = _code_search_fallback(pattern, max_results)
    else:
        hits = _code_search_fallback(pattern, max_results)
    if not hits:
        return f"No matches for {pattern!r} in {', '.join(roots)}."
    return "\n".join(hits)
