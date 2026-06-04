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
import os
import re
import socket
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
        return f"Fetch failed: {type(exc).__name__}: {exc}"
    return _to_text(raw) or "(empty page)"


def mcp_asgi_app():
    """The Streamable-HTTP ASGI app to mount under /mcp."""
    return mcp.streamable_http_app()


def session_manager():
    """FastMCP's streamable-HTTP session manager. The parent ASGI app MUST run
    this in its lifespan (`async with session_manager().run(): ...`) or requests
    fail with 'Task group is not initialized'."""
    return mcp.session_manager
