"""Usage/cost telemetry -- record per-call tokens + model so cost claims become
scorable.

Every model call in argo_observe goes through one _guarded(...) response; this
module normalizes that response's `usage` across the three providers Argo speaks
and appends one row to a volume-capable ledger (ARGO_COST_LEDGER_PATH). The point
(ROADMAP Stage 2 P0): the prompt-caching (PR #30) and Batch wins become MEASURED,
not asserted -- summarize(by='model') gives the totals a later prediction can be
scored against.

Provider usage shapes we normalize (capture what's present, default the rest to 0
-- a missing field must never crash a chat turn):
  - anthropic (messages.create / beta.messages.create):
      usage.input_tokens, usage.output_tokens,
      usage.cache_creation_input_tokens, usage.cache_read_input_tokens
  - openai chat-completions (also xai/grok, OpenAI-compatible):
      usage.prompt_tokens, usage.completion_tokens
      (no first-class cache fields on this API today)
  - openai Responses API (_chat_with_mcp_openai):
      usage.input_tokens, usage.output_tokens,
      usage.input_tokens_details.cached_tokens (cache reads)

CRITICAL contract: telemetry must NEVER crash a chat turn. record_usage() wraps
the whole normalize+append in a try/except that logs (exc_info=True) and swallows
-- a ledger-write failure (disk full, corrupt store, an unexpected usage shape)
is a missed measurement, never a failed call. Callers therefore need no extra
guard of their own.

Backed by the volume-capable ARGO_COST_LEDGER_PATH (see argo_paths); stdlib + the
shared argo_store I/O and argo_log only.
"""

import threading
import time

import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# Module-level so tests can patch it (mock.patch.object(argo_cost, "LEDGER_PATH",
# tmp)); record_usage/summarize read this global at call time so the override
# bites -- never read argo_paths.COST_LEDGER_PATH directly inside a helper.
LEDGER_PATH = argo_paths.COST_LEDGER_PATH

# Serializes the read-modify-write of record_usage(). Argo's model calls fan out
# across the webhook's background chat threads (each turn can make several), so an
# overlapping load_json -> append -> save_json could drop a row. An in-process lock
# is sufficient (the same convention as argo_pushes); argo_store still does the
# atomic save underneath.
_write_lock = threading.Lock()


def _g(usage, name):
    """Read a token field off a usage object (or dict), defaulting to 0.

    SDK usage objects expose attributes; a hand-built dict (tests, future shapes)
    exposes keys -- handle both. A None/absent/non-numeric value normalizes to 0 so
    a missing field never propagates into the arithmetic or the record."""
    val = getattr(usage, name, None)
    if val is None and isinstance(usage, dict):
        val = usage.get(name)
    return val if isinstance(val, (int, float)) else 0


def normalize(usage, provider):
    """Normalize a provider's `usage` into a common token dict.

    Returns input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
    (all ints, 0 when the provider/field is absent). Captures whatever the response
    actually carried; never raises on a missing field.

    anthropic carries input/output_tokens + the two cache_* fields directly. The
    The OpenAI/xAI chat-completions API uses prompt/completion_tokens and nests cache
    reads under prompt_tokens_details.cached_tokens; the OpenAI Responses API uses
    input/output_tokens and nests cache reads under input_tokens_details.cached_tokens.
    We read every variant and take the one that is present, so the same record shape
    covers all three providers."""
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0,
                "cache_creation_tokens": 0, "cache_read_tokens": 0}

    # input/output: chat-completions names them prompt/completion; anthropic and the
    # OpenAI Responses API both use input/output. Take whichever is non-zero.
    input_tokens = _g(usage, "input_tokens") or _g(usage, "prompt_tokens")
    output_tokens = _g(usage, "output_tokens") or _g(usage, "completion_tokens")

    # cache: anthropic exposes creation + read directly; the OpenAI Responses API
    # nests cached reads under input_tokens_details.cached_tokens and the OpenAI/xAI
    # chat-completions API under prompt_tokens_details.cached_tokens.
    cache_creation = _g(usage, "cache_creation_input_tokens")
    cache_read = _g(usage, "cache_read_input_tokens")
    if not cache_read:
        for details_field in ("input_tokens_details", "prompt_tokens_details"):
            details = getattr(usage, details_field, None)
            if details is None and isinstance(usage, dict):
                details = usage.get(details_field)
            if details is not None:
                cache_read = _g(details, "cached_tokens")
                if cache_read:
                    break

    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_creation_tokens": int(cache_creation),
        "cache_read_tokens": int(cache_read),
    }


def record_usage(response, model, provider, label, ts=None):
    """Append one telemetry row for a completed model call. NEVER raises.

    `response` is the SDK response object (we read response.usage); `provider` is the
    provider name ('anthropic'/'openai'/'xai'); `label` is the short call site (e.g.
    'chat/claude-opus-4-8', the same string passed to _guarded). Wraps the whole
    normalize+append in try/except that logs (exc_info=True) and swallows: a ledger
    failure is a missed measurement, never a failed chat turn. Returns the row dict
    on success, or None if recording was skipped/failed."""
    try:
        usage = getattr(response, "usage", None)
        tokens = normalize(usage, provider)
        row = {
            "ts": ts if ts is not None else time.time(),
            "model": model,
            "provider": provider,
            "label": label,
            **tokens,
        }
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Lock the load->append->save so a concurrent record_usage in the same
        # webhook process can't drop a row.
        with _write_lock:
            rows = argo_store.load_json(LEDGER_PATH, [])
            if not isinstance(rows, list):
                rows = []
            rows.append(row)
            argo_store.save_json(LEDGER_PATH, rows)
        log.debug("recorded cost row model=%s in=%d out=%d cache_read=%d",
                  model, tokens["input_tokens"], tokens["output_tokens"],
                  tokens["cache_read_tokens"])
        return row
    except Exception:
        # Non-fatal by contract: an un-recorded call is a missed measurement, never
        # a failed model call. Log so the operator sees a ledger problem.
        log.warning("cost telemetry record failed (non-fatal) model=%s label=%s",
                    model, label, exc_info=True)
        return None


def _load():
    """Return the ledger as a list (empty on missing/corrupt/wrong-shape)."""
    rows = argo_store.load_json(LEDGER_PATH, [])
    return rows if isinstance(rows, list) else []


def summarize(by="model"):
    """Aggregate the ledger into per-key token totals for prediction-scoring.

    `by` is the row field to group on ('model' or 'provider'). Returns
    {key: {calls, input_tokens, output_tokens, cache_creation_tokens,
    cache_read_tokens}}. Pure read; never writes."""
    fields = ("input_tokens", "output_tokens",
              "cache_creation_tokens", "cache_read_tokens")
    out = {}
    for r in _load():
        key = r.get(by) or "unknown"
        agg = out.setdefault(key, {"calls": 0, **{f: 0 for f in fields}})
        agg["calls"] += 1
        for f in fields:
            v = r.get(f, 0)
            agg[f] += v if isinstance(v, (int, float)) else 0
    return out
