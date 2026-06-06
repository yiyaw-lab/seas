"""Shared HTTP helpers for Argo's urllib callers. Currently just the TLS context.

Every outbound call (Telegram, GitHub, feeds, Firecrawl) built the same SSL
context the same way: prefer certifi's CA bundle so TLS verifies on hosts whose
system trust store is thin (Railway, Actions), fall back to the system default if
certifi isn't installed. That block was copy-pasted in seven places. One function
now.

Deliberately NOT a full request wrapper: the per-call timeouts are meaningfully
different (Telegram 30s, GitHub 10s, a webhook health check 6s, feeds 20s) and so
is the error handling, so a one-size get_json/post_json would either flatten those
or hide them. If a request helper ever earns its keep, it belongs here -- but keep
timeout an explicit per-call argument. Stdlib + certifi (already a dep).
"""

import ssl


def tls_context():
    """An SSL context that verifies against certifi's CA bundle when available,
    else the system default. Use for every outbound HTTPS call."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
