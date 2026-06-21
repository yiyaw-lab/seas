"""Anthropic Message Batches primitive for bulk SEAS scoring (EV-003).

The third Stage-1 seed (ROADMAP.md): SEAS scoring is the highest-volume,
latency-tolerant LLM work in the system — auto_score_signals fires one
sequential Claude call per unscored signal. The Batches API processes many
Messages requests asynchronously at 50% of standard prices, which is exactly
the right shape for scoring: we don't need the result this second, and there
can be dozens of signals.

This module is a MINIMAL, OPT-IN primitive — it changes nothing by default.
A caller that wants cheap bulk scoring assembles N (custom_id, prompt) pairs,
calls `run_batch`, and gets back a {custom_id: result} map. Wiring it into the
live `seas_finding.auto_score_signals` loop is DEFERRED (see module note at the
bottom): the primitive + its test are the load-bearing piece; the live swap is
a separate, larger change with its own dry-run/rehearse gate.

How the Batches API works (anthropic SDK >= the version exposing
`client.messages.batches`):
  1. create  — POST a list of requests, each {custom_id, params}; returns a batch id
  2. poll    — retrieve the batch until processing_status == "ended"
  3. results — stream results keyed by custom_id; each is succeeded/errored/...

Design choices (deliberately small):
  - Anthropic-only. SEAS scoring runs on Claude (Sonnet by default); the OpenAI
    batch endpoint is a different shape and is not in scope.
  - claude-opus-4-8 rejects `temperature` (the API 400s) — we reuse
    argo_observe._rejects_temperature and OMIT the param, matching the single
    -call path. No temperature is ever sent unless the model accepts it.
  - The poll loop is BOUNDED: a max wall-clock deadline and a capped sleep, so a
    stuck batch can never spin forever (real batches usually finish < 1h; the
    API's own ceiling is 24h).
  - Partial failures are SURFACED, not dropped: an errored/expired/canceled item
    comes back as a BatchItemResult with ok=False and the error, so the caller
    can see exactly which custom_ids failed.

Reuses argo_log.get_logger for operator visibility. TLS is the anthropic SDK's
own (it bundles certifi); we don't hand-roll urllib here, so argo_http isn't
needed on this path.
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

from argo_log import get_logger

log = get_logger(__name__)

# Poll-loop bounds. Real batches usually complete within an hour; the API's hard
# ceiling is 24h. For SEAS scoring (a daily cadence) a default 1h wall-clock cap
# with a 30s poll interval is plenty and keeps a stuck batch from spinning
# forever. Both are overridable per call for tests (which monkeypatch sleep).
DEFAULT_POLL_INTERVAL_S = 30.0
DEFAULT_MAX_WAIT_S = 3600.0


@dataclass
class BatchItemResult:
    """One request's outcome, keyed back to its custom_id.

    ok=True  -> `text` is the model's joined text output, `status` == "succeeded".
    ok=False -> `error` describes why (errored/expired/canceled, or an unexpected
                result shape); `text` is None. Surfaced, never silently dropped.
    """

    custom_id: str
    ok: bool
    status: str
    text: Optional[str] = None
    error: Optional[str] = None


def _client():
    """An Anthropic client with a stripped key (a trailing newline/space in a CI
    secret makes an illegal Authorization header — see argo_observe._call_anthropic)."""
    import anthropic  # lazy: callers without the SDK/key never import it

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


def build_requests(items, model, system=None, max_tokens=1024):
    """Assemble N scoring prompts into Batches request objects, one per item.

    `items` is an iterable of (custom_id, prompt) pairs. custom_ids MUST be
    unique within a batch — results come back keyed by custom_id, never by
    position, so a collision would conflate two signals. We assert uniqueness
    here rather than let the API silently keep the last writer.

    Each request carries the same per-call shape as the single-call path:
    optional system prompt, one user turn, and NO temperature for models that
    reject it (claude-opus-4-8 400s on the param).
    """
    from anthropic.types.message_create_params import (
        MessageCreateParamsNonStreaming,
    )
    from anthropic.types.messages.batch_create_params import Request

    # Lazy import to avoid a hard dep on argo_observe at module load; we only
    # need its temperature guard, and reusing it keeps the rule in one place.
    import argo_observe

    requests = []
    seen = set()
    for custom_id, prompt in items:
        if custom_id in seen:
            raise ValueError(f"duplicate custom_id in batch: {custom_id!r}")
        seen.add(custom_id)

        params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            params["system"] = system
        # Omit temperature for models that reject a custom value (opus-4-8 et al.);
        # the Batches request must be a valid Messages request, so the same 400
        # applies here.
        if not argo_observe._rejects_temperature(model):
            params["temperature"] = 1.0

        requests.append(
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(**params),
            )
        )
    return requests


def _join_text(message):
    """Join the text blocks of a Batches result message (mirrors the single-call
    path's content-join)."""
    return "".join(
        block.text for block in message.content if block.type == "text"
    )


def run_batch(items, model, *, system=None, max_tokens=1024,
              poll_interval_s=DEFAULT_POLL_INTERVAL_S,
              max_wait_s=DEFAULT_MAX_WAIT_S, client=None, sleep=time.sleep):
    """Score N items in one batch at 50% cost. Returns {custom_id: BatchItemResult}.

    Steps: build one batch of N requests -> create -> poll until `ended` (bounded
    by max_wait_s) -> map results back BY custom_id. A partial failure (an item
    that errored/expired/canceled, or any unexpected result shape) is surfaced as
    a BatchItemResult with ok=False, never dropped — the caller decides whether to
    retry or fall back to the single-call path for those ids.

    `client` and `sleep` are injectable for testing (no network, no real waits).
    Raises TimeoutError if the batch hasn't ended within max_wait_s.
    """
    items = list(items)
    if not items:
        return {}

    requests = build_requests(items, model, system=system, max_tokens=max_tokens)
    client = client or _client()

    batch = client.messages.batches.create(requests=requests)
    log.info("batch created id=%s n=%d model=%s", batch.id, len(requests), model)

    # Bounded poll: monotonic deadline so wall-clock can't run away. The first
    # status may already be "ended" (tiny batches / mocked clients).
    deadline = time.monotonic() + max_wait_s
    status = getattr(batch, "processing_status", None)
    while status != "ended":
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"batch {batch.id} did not end within {max_wait_s:.0f}s "
                f"(last status: {status})"
            )
        sleep(poll_interval_s)
        batch = client.messages.batches.retrieve(batch.id)
        status = getattr(batch, "processing_status", None)
        log.info("batch %s status=%s", batch.id, status)

    # Map results back BY custom_id (results arrive in any order — never index by
    # position). Surface failed/errored items rather than dropping them.
    out = {}
    for result in client.messages.batches.results(batch.id):
        rtype = result.result.type
        if rtype == "succeeded":
            out[result.custom_id] = BatchItemResult(
                custom_id=result.custom_id, ok=True, status=rtype,
                text=_join_text(result.result.message),
            )
        else:
            # errored / expired / canceled (or anything unexpected). Pull a best
            # -effort error string; the shape differs per type, so guard.
            err = getattr(getattr(result.result, "error", None), "type", None) \
                or rtype
            out[result.custom_id] = BatchItemResult(
                custom_id=result.custom_id, ok=False, status=rtype, error=str(err),
            )
            log.warning("batch %s item %s failed: %s",
                        batch.id, result.custom_id, err)

    return out


# ---------------------------------------------------------------------------
# DEFERRED: wiring this into seas_finding.auto_score_signals.
#
# auto_score_signals currently scores signals one Claude call at a time. The
# drop-in is: collect the unscored signals into (signal_id, prompt) pairs, call
# run_batch, then map BatchItemResult.text back through the same _extract_json +
# 5-dimension validation the loop already does. We DEFER that here because:
#   - it changes a live research path (the scored-prediction milestone rides on
#     it), so it deserves its own dry-run/rehearse gate and its own PR, not a
#     rider on the primitive;
#   - batches are asynchronous (minutes, not ms), so the caller's control flow
#     changes shape (the `seas` CLI step would block on the poll, or hand off) —
#     a real design decision, not a mechanical swap.
# The primitive + its unit test are the minimal, verifiable capability; the
# integration is explicitly out of scope for this seed.
# ---------------------------------------------------------------------------
