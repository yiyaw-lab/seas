"""Operator-facing logging for Argo. Standard-library `logging`, one config.

Use `get_logger(__name__)` on operational and error paths — scheduler firing
decisions, seen-store outcomes, Telegram delivery failures, guard/breaker/budget
events — so the next failure leaves a trace the operator can read (Railway /
GitHub Actions console) instead of having to be reproduced by hand.

This is NOT for user-facing Telegram text: that still goes through
argo_webhook._clean_reply + print/return. Logs are for the operator only.

Level comes from ARGO_LOG_LEVEL (default INFO). The format is plain text, which
both Railway and Actions render and grep fine; if structured JSON is ever needed
for log search, it's a small custom Formatter drop-in here — don't add it
speculatively.
"""

import logging
import os

_CONFIGURED = False


def get_logger(name):
    """Return a module logger, configuring the root handler once on first call."""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=os.environ.get("ARGO_LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        _CONFIGURED = True
    return logging.getLogger(name)
