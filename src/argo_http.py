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

from argo_log import get_logger

log = get_logger(__name__)


def tls_context():
    """An SSL context that verifies against certifi's CA bundle when available,
    else the system default. Use for every outbound HTTPS call."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get_bytes(url, *, timeout, retries=1, headers=None):
    """Fetch raw bytes over HTTP(S) with certifi TLS and conservative transient
    retry. The request helper the module docstring left room for.

    Retries ONLY errors argo_guard classifies as transient (timeouts, connection
    resets, 429/5xx); permanent failures (401/404/billing-400) fail fast on the
    first attempt so a dead URL or bad token is not hammered. Backoff + jitter
    between attempts, reusing argo_guard's delay tunables so retry timing stays in
    one place.

    timeout is a REQUIRED per-call argument (this layer never picks a default —
    see the module docstring). Keep retries low: callers run inside short
    @with_deadline budgets, so timeout * (retries + 1) plus backoff must stay
    under the cap (e.g. timeout<=8, retries=1 ~= 17s under a 20s deadline).
    """
    import random
    import time
    import urllib.request

    import argo_guard  # late import: keeps argo_http free of a module-level dep

    req = urllib.request.Request(url, headers=headers or {})
    ctx = tls_context()
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except Exception as exc:
            attempt += 1
            if attempt > retries or not argo_guard._is_transient(exc):
                raise
            delay = min(argo_guard.BASE_DELAY * (2 ** (attempt - 1)),
                        argo_guard.MAX_DELAY)
            delay += random.uniform(0, delay * 0.25)  # jitter
            log.warning("get_bytes: transient %s on %s, retry %d/%d in %.1fs",
                        type(exc).__name__, url, attempt, retries, delay)
            time.sleep(delay)
