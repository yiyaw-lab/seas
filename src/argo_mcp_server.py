"""
Argo's custom MCP server (Phase B+): tools Argo's chat can call.

Built with the `mcp` SDK's FastMCP, exposed over Streamable HTTP so the Anthropic
MCP connector (beta mcp-client-2025-11-20) can reach it. Mounted into the main
service under /mcp via ASGI (see argo_webhook.create_asgi_app).

Phase B exposes ONE read-only tool:
  - web_fetch(url): fetch + readable-text a page, but ONLY from an approved host
    allowlist (derived from fetch_signals.FEEDS + a few frontier read domains).
    The allowlist is enforced HERE, by us — never by the model. Doubles as SSRF
    defense (no internal IPs / metadata endpoints).

Auth: the connector passes `authorization_token` as a Bearer header; we require
it to equal ARGO_MCP_TOKEN. Enforced by the ASGI wrapper in argo_webhook.

Reuses, doesn't duplicate: fetch_signals._fetch_url (urllib + certifi TLS).
Later phases add repo-read (C), self-status (D), and gated self-heal (E) tools.
"""

import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

import fetch_signals

MAX_FETCH_CHARS = 6000  # keep tool results small; this is a scout, not a scraper

# Approved hosts. Start from the FEEDS allowlist (the existing approved-sources
# pattern), then add the frontier read-domains we want full-page access to.
def _build_allowed_hosts():
    hosts = set()
    for _label, url in fetch_signals.FEEDS:
        host = urlparse(url).hostname
        if host:
            hosts.add(host.lower())
    hosts.update({
        "arxiv.org", "export.arxiv.org",
        "github.com", "raw.githubusercontent.com",
        "huggingface.co",
        "openai.com",
        "anthropic.com", "www.anthropic.com",  # no RSS feed, but /news fetches fine
        "x.ai", "docs.x.ai",  # x.ai/news hard-blocks bots (403); docs.x.ai fetches
        "blog.google",
        "github.blog",
    })
    return hosts


ALLOWED_HOSTS = _build_allowed_hosts()


def _host_allowed(host):
    """True if host is in the allowlist (exact or a subdomain of an entry)."""
    if not host:
        return False
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _resolves_to_public_ip(host):
    """SSRF guard: reject hosts that resolve to private/loopback/link-local IPs."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _to_text(raw):
    """Strip tags/scripts to readable text, capped."""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_FETCH_CHARS]


# FastMCP's Streamable-HTTP transport enforces DNS-rebinding protection: it
# rejects requests whose Host header isn't in allowed_hosts (defaults to
# localhost only), returning 421. Behind Railway the Host is our public domain,
# so we must allow it or the Anthropic connector's calls are refused. Derive the
# host from WEBHOOK_URL; allow it (and its :port form) plus localhost for dev.
def _transport_security():
    from urllib.parse import urlparse
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    base = os.environ.get("WEBHOOK_URL")
    if base:
        host = urlparse(base).hostname
        if host:
            hosts += [host, f"{host}:*"]
            origins += [f"https://{host}", f"https://{host}:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


mcp = FastMCP("argo", transport_security=_transport_security())


@mcp.tool()
def web_fetch(url: str) -> str:
    """Fetch a web page from Argo's approved source list and return its readable
    text. Use this to read the actual content of a frontier source (a paper, a
    repo readme, a changelog, a blog post) rather than guessing. Only approved
    hosts (arXiv, GitHub, Hugging Face, OpenAI, Google AI, and Argo's feeds) are
    allowed; anything else is refused."""
    if not url.lower().startswith("https://"):
        return "Refused: only https:// URLs are allowed."
    host = urlparse(url).hostname
    if not _host_allowed(host):
        return (
            f"Refused: '{host}' is not on Argo's approved source list. "
            f"Allowed hosts include: {', '.join(sorted(ALLOWED_HOSTS))}."
        )
    if not _resolves_to_public_ip(host):
        return f"Refused: '{host}' did not resolve to a public address."
    try:
        raw = fetch_signals._fetch_url(url, timeout=20)
    except Exception as exc:
        msg = f"Fetch failed: {type(exc).__name__}: {exc}"
        # Many sites (e.g. openai.com) 403 automated HTML requests but serve
        # their RSS feed fine. Point Argo at the feed for this host if we have it.
        if "403" in str(exc):
            feed = _feed_for_host(host)
            if feed:
                msg += (f"  This host blocks page scraping; use its feed instead: "
                        f"{feed} (call web_fetch on that).")
        return msg
    return _to_text(raw) or "(empty page)"


def _feed_for_host(host):
    """Return an approved feed URL whose host matches, if any."""
    if not host:
        return None
    host = host.lower()
    for _label, url in fetch_signals.FEEDS:
        h = (urlparse(url).hostname or "").lower()
        if h == host or host.endswith("." + h) or h.endswith("." + host):
            return url
    return None


@mcp.tool()
def list_feeds() -> str:
    """List Argo's approved RSS/Atom feed URLs (arXiv, GitHub trending, OpenAI,
    Hugging Face, Google AI, GitHub changelog). Prefer fetching these feeds over
    HTML pages: feeds are reliable and machine-readable, while many sites block
    automated page requests. Use this when the user asks 'what's new on X'."""
    return "\n".join(f"{label}: {url}" for label, url in fetch_signals.FEEDS)


# --- Phase C: repo/code read (GitHub) ---------------------------------------
# Repos Argo may read. Comma-separated owner/repo in GITHUB_REPO_ALLOWLIST; "*"
# (the default) allows ANY repo so Argo can read trending/other repos it surfaces.
# Reads are read-only and size-capped (same risk class as web_fetch). Public
# repos need no token; private repos require GITHUB_TOKEN. Set the env var to a
# specific list if you ever want to restrict it.
def _repo_allowlist():
    raw = os.environ.get("GITHUB_REPO_ALLOWLIST", "*")
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def _repo_allowed(repo):
    allow = _repo_allowlist()
    return "*" in allow or repo.lower() in allow


def _gh_api(path, raw=False):
    """Call the GitHub REST API; return (ok, text). Uses GITHUB_TOKEN if set."""
    import ssl
    import urllib.request

    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    headers = {
        "User-Agent": "argo-mcp/1.0",
        "Accept": "application/vnd.github.raw+json" if raw
                  else "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token.strip()

    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return True, r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        hint = ""
        if "404" in str(exc):
            hint = " (private repo? needs GITHUB_TOKEN, or wrong path)"
        return False, f"GitHub API error: {type(exc).__name__}: {exc}{hint}"


@mcp.tool()
def github_read_file(repo: str, path: str) -> str:
    """Read a file from a GitHub repo. `repo` is 'owner/name', `path` is the file
    path within it. Use this to read actual source/README/config of any project,
    especially a trending repo you just surfaced, instead of guessing."""
    if "/" not in repo:
        return "Refused: repo must be 'owner/name'."
    if not _repo_allowed(repo):
        return (f"Refused: '{repo}' is not on Argo's approved repo list "
                f"({', '.join(sorted(_repo_allowlist()))}).")
    ok, body = _gh_api(f"/repos/{repo}/contents/{path.lstrip('/')}", raw=True)
    if not ok:
        return body
    return body[:MAX_FETCH_CHARS] or "(empty file)"


@mcp.tool()
def github_list(repo: str, path: str = "") -> str:
    """List files/dirs in a GitHub repo at `path` (default: root). `repo` is
    'owner/name'. Use to explore a repo's structure (e.g. a trending project you
    surfaced) before reading a specific file."""
    import json as _json

    if "/" not in repo:
        return "Refused: repo must be 'owner/name'."
    if not _repo_allowed(repo):
        return (f"Refused: '{repo}' is not on Argo's approved repo list "
                f"({', '.join(sorted(_repo_allowlist()))}).")
    ok, body = _gh_api(f"/repos/{repo}/contents/{path.lstrip('/')}")
    if not ok:
        return body
    try:
        entries = _json.loads(body)
    except (ValueError, _json.JSONDecodeError):
        return "(could not parse listing)"
    if isinstance(entries, dict):  # a file path, not a dir
        return f"{entries.get('name')} (file, {entries.get('size')} bytes)"
    return "\n".join(f"{e['type']:4s}  {e['name']}" for e in entries) or "(empty)"


# --- Phase D: self-status (read-only, autonomy L0) --------------------------
# Tools that let Argo report its OWN health. Read-only: they observe, never act
# (self-heal actions are Phase E). Argo can diagnose and tell you what to do.

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_LOG = ROOT / "data" / "argo_projects.json"
SIGNALS_PATH = ROOT / "data" / "signals.json"


@mcp.tool()
def get_webhook_health() -> str:
    """Report Argo's own Telegram webhook status: the registered URL, pending
    update count, and the last delivery error (if any). Use when asked 'are you
    healthy / is the bot working / why might messages be dropping'."""
    import json as _json
    import ssl
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return "TELEGRAM_BOT_TOKEN not set, can't check webhook."
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    try:
        url = f"https://api.telegram.org/bot{token.strip()}/getWebhookInfo"
        with urllib.request.urlopen(url, timeout=15, context=ctx) as r:
            info = _json.loads(r.read().decode()).get("result", {})
    except Exception as exc:
        return f"Couldn't reach Telegram: {type(exc).__name__}: {exc}"
    parts = [
        f"url: {info.get('url') or '(none set!)'}",
        f"pending updates: {info.get('pending_update_count', 0)}",
    ]
    if info.get("last_error_message"):
        parts.append(f"last error: {info['last_error_message']}")
    return "; ".join(parts)


@mcp.tool()
def get_latest_project() -> str:
    """Report Argo's most recent weekly project and its energy rating (or that
    it's unrated). Use when asked 'what did you suggest last / what's my latest
    project / did I rate it'."""
    import json as _json

    if not PROJECTS_LOG.exists():
        return "No projects logged yet."
    try:
        log = _json.loads(PROJECTS_LOG.read_text())
    except (ValueError, _json.JSONDecodeError):
        return "Project log is unreadable."
    if not log:
        return "No projects logged yet."
    p = log[-1]
    energy = p.get("energy")
    rating = f"energy {energy}/10" if energy is not None else "not yet rated"
    return f"{p.get('id')} ({p.get('date')}, {rating}):\n{p.get('text','')[:800]}"


@mcp.tool()
def get_signal_freshness() -> str:
    """Report how fresh Argo's signal pool is: when signals.json was last
    refreshed and how many signals it holds. Use when asked 'how current are your
    signals / when did you last pull'."""
    import json as _json
    from datetime import datetime, timezone

    if not SIGNALS_PATH.exists():
        return "No signals file yet (signals are fetched fresh per run)."
    mtime = datetime.fromtimestamp(SIGNALS_PATH.stat().st_mtime, tz=timezone.utc)
    age_h = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    try:
        n = len(_json.loads(SIGNALS_PATH.read_text()))
    except (ValueError, _json.JSONDecodeError):
        n = "?"
    return (f"{n} signals, last refreshed {mtime:%Y-%m-%d %H:%M UTC} "
            f"({age_h:.1f}h ago).")


# --- Phase E2/E3: self-heal ACTIONS (gated by ARGO_HEAL_LEVEL) --------------
# L0 (default): report-only. The tools describe the fix and refuse to execute.
# L1: the tool stages a pending action and tells the user to reply CONFIRM in
#     Telegram; the webhook's CONFIRM shortcut then runs it. Never auto-acts.
# Only idempotent, non-destructive heals live here (reregister webhook, refetch
# signals). No restart, no delete, no broadcast, no credential changes.

HEAL_LEVEL = os.environ.get("ARGO_HEAL_LEVEL", "L0").upper()
PENDING_HEAL_PATH = ROOT / "data" / "argo_pending_heal.json"

# Registry of allowed heal actions: name -> (human description, callable).
# Callables are imported lazily inside _run_heal to avoid import cycles.
HEAL_ACTIONS = {
    "reregister_webhook": "re-register the Telegram webhook to WEBHOOK_URL",
    "refetch_signals": "refetch the frontier signal feeds",
}


def _stage_pending(action):
    """Record a single pending heal action for the CONFIRM shortcut to pick up."""
    from datetime import datetime, timezone
    PENDING_HEAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_HEAL_PATH.write_text(json.dumps({
        "action": action,
        "staged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }))


def _heal(action):
    """Shared logic for a heal tool. At L0 reports; at L1 stages + asks CONFIRM."""
    if action not in HEAL_ACTIONS:
        return f"Unknown heal action '{action}'."
    desc = HEAL_ACTIONS[action]
    if HEAL_LEVEL == "L1":
        _stage_pending(action)
        return (f"I can {desc}. Reply CONFIRM to let me do it, or CANCEL to drop "
                f"it. (This is the only action I'll run, and only on your okay.)")
    # L0 default: report-only, never execute.
    cmd = {"reregister_webhook": "python3 src/set_webhook.py "
           "https://argo.up.railway.app/webhook",
           "refetch_signals": "python3 src/fetch_signals.py"}.get(action, "")
    return (f"Recommended fix: {desc}. I'm in report-only mode, so I won't run it "
            f"myself. You can: {cmd}")


@mcp.tool()
def reregister_webhook() -> str:
    """Heal action: re-register Argo's Telegram webhook (use when get_webhook_health
    shows no URL or a delivery error). Idempotent and safe. Honors the autonomy
    level: report-only by default, or asks for CONFIRM at L1."""
    return _heal("reregister_webhook")


@mcp.tool()
def refetch_signals() -> str:
    """Heal action: refetch the frontier signal feeds (use when get_signal_freshness
    shows the pool is stale). Idempotent and safe. Honors the autonomy level:
    report-only by default, or asks for CONFIRM at L1."""
    return _heal("refetch_signals")


def run_pending_heal():
    """Execute the staged heal action (called by the webhook CONFIRM shortcut at
    L1). Returns a status string. Clears the pending file either way."""
    if not PENDING_HEAL_PATH.exists():
        return "Nothing staged to confirm."
    try:
        pending = json.loads(PENDING_HEAL_PATH.read_text())
    except (ValueError, json.JSONDecodeError):
        PENDING_HEAL_PATH.unlink(missing_ok=True)
        return "Pending action was unreadable; cleared it."
    action = pending.get("action")
    PENDING_HEAL_PATH.unlink(missing_ok=True)  # one-shot, clear before running
    try:
        if action == "reregister_webhook":
            import argo_webhook
            argo_webhook.self_register_webhook()
            return "Re-registered the webhook. ✅"
        if action == "refetch_signals":
            import fetch_signals
            fetch_signals.main()
            return "Refetched the signal feeds. ✅"
        return f"Unknown staged action '{action}'."
    except Exception as exc:
        return f"Heal action '{action}' failed: {type(exc).__name__}: {exc}"


def clear_pending_heal():
    PENDING_HEAL_PATH.unlink(missing_ok=True)


def mcp_asgi_app():
    """The Streamable-HTTP ASGI app to mount under /mcp."""
    return mcp.streamable_http_app()


def session_manager():
    """FastMCP's streamable-HTTP session manager. The parent ASGI app MUST run
    this in its lifespan (`async with session_manager().run(): ...`) or requests
    fail with 'Task group is not initialized'."""
    return mcp.session_manager
