"""
Argo V2 — Phase A: Observation generator (sidecar).

Generates OBSERVATIONS only — the bottom of the V2 funnel (see ARGO_V2.md,
ARGO_V2_MIGRATION.md). An Observation is a noticing: a true, specific statement
about what the field is paying attention to, and what it is walking past.

This script does NOT fake insight. It always assembles a complete, reusable
observation-generation job from real inputs, and:

  - if a usable API key is set (and the matching SDK is installed): it sends the
    job to the model and writes the observations to latest.md + a dated copy;
  - otherwise: it writes the job-only placeholder and prints a clear message —
    it never crashes and never fabricates observations.

Steps:
  1. load 2-3 signals + F-001 as context,
  2. assemble the "everyone / but" prompt + the real inputs (the job),
  3. write the job to argo/observations/observation_job.md,
  4. with a key: call the LLM -> argo/observations/latest.md + YYYY-MM-DD.md,
     without a key: leave latest.md as a placeholder,
  5. print to the terminal.

Config (read from a .env file via python-dotenv, or the environment):
  OPENAI_API_KEY      auth for gpt-* / o* models
  ANTHROPIC_API_KEY   auth for claude-* models
  ARGO_MODEL          model override; its name routes the provider
                      (default tries gpt-4.1, then gpt-4o)
Providers are not hardcoded into the flow — they live in the PROVIDERS registry
and are selected by the model name.

Phase A scope only. Does NOT: select a bet, write data/argo_bets.json, track
energy, touch Argo V1 (argo.py), or wire Telegram. Standalone — imports nothing
from argo.py.

Run with:  python src/argo_observe.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

import argo_cost
import argo_guard
import argo_paths
from argo_log import get_logger
from argo_observe_cache_patch import system_with_cache

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = argo_paths.SIGNALS_PATH  # single source of truth (see argo_paths)
FINDING_PATH = ROOT / "findings" / "F-001-cognitive-operators.md"
OUT_DIR = ROOT / "argo" / "observations"

# Resilience guardrails (Phase E1): a circuit breaker per provider + a global
# daily call budget. Wrap every model call so transient failures retry, a dead
# provider fails fast, and a runaway loop can't blow past the daily cap.
_BREAKERS = {
    "openai": argo_guard.CircuitBreaker("openai"),
    "anthropic": argo_guard.CircuitBreaker("anthropic"),
    "xai": argo_guard.CircuitBreaker("xai"),
}
_BUDGET = argo_guard.DailyBudget()

# Load .env (OPENAI_API_KEY / ANTHROPIC_API_KEY / ARGO_MODEL) if python-dotenv
# is installed. Optional: if the package is missing we fall back to the real
# environment, so the script never hard-depends on dotenv.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

TODAY = datetime.now().strftime("%Y-%m-%d")

# How many signals to feed in (Phase A: lean input, 2-3) and how many
# observations the job should ask for.
NUM_SIGNALS = 3
NUM_OBSERVATIONS = 7

# Model selection. ARGO_MODEL wins if set; otherwise try these in order.
# The model name routes the provider (see PROVIDERS); we do not hardcode a
# provider anywhere else.
DEFAULT_MODELS = ("gpt-4.1", "gpt-4o")


# ---------------------------------------------------------------------------
# Instructions — the generation method, not the output.
# This is the "everyone / but" move from ARGO_V2.md. It tells the model HOW to
# notice; it does NOT contain any pre-written observations.
# ---------------------------------------------------------------------------

INSTRUCTIONS = f"""You are Argo, a frontier scout. Your job in this task is to
NOTICE — to generate {NUM_OBSERVATIONS} original Observations about the frontier
signals below.

An Observation is:
- a true, specific statement about what the field is paying ATTENTION to —
  and, just as importantly, what it is walking past;
- descriptive, not prescriptive (it does NOT recommend a project);
- often slightly obvious-in-hindsight: "...huh, yeah, that IS true."

Use the "everyone / but" move:
  "Everyone is focused on X. But the thing that may actually matter is Y."
X comes from the signals. Y is the leap — that is where originality lives.

Also try:
- crossing two signals against each other (what pattern do they share?);
- inverting the consensus (what's true if the opposite is?);
- naming the blind spot next to where everyone is looking.

Rules:
- Generate {NUM_OBSERVATIONS} DISTINCT observations. Quantity first; most will be
  mediocre, that is expected.
- Do NOT propose projects, bets, or actions. Observations only.
- Each observation: 1-3 short sentences. No headings.
- Aim for at least one that the reader would NOT have thought of themselves.

Output format: a numbered list, 1 to {NUM_OBSERVATIONS}, nothing else.
"""


def load_signals(limit=NUM_SIGNALS):
    """Load the top `limit` signals. Defaults to NUM_SIGNALS (3) for the
    observation path, which wants a tight slice; the project generator passes a
    larger limit so it has real material to synthesize a bet from.

    signals.json is gitignored, so it's absent on a fresh deploy until the first
    fetch runs. Return [] rather than crashing, so callers degrade to 'no
    signals' (and can refetch) instead of erroring into a confused self-heal."""
    if not SIGNALS_PATH.exists():
        return []
    try:
        signals = json.loads(SIGNALS_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return []
    return signals[:limit]


def format_signals(signals):
    lines = []
    for i, s in enumerate(signals, start=1):
        lines.append(
            f"{i}. {s['title']}\n"
            f"   Source: {s.get('source', '')}\n"
            f"   Category: {s.get('category', '')}\n"
            f"   Summary: {s.get('summary', '')}"
        )
    return "\n\n".join(lines)


def build_job(signals_block, signal_count, finding_text):
    """Assemble the full, self-contained observation-generation job."""
    return f"""# Argo Observation Job — {TODAY}

Phase A (ARGO_V2_MIGRATION.md): generate observations only. No bet, no selection.

---

## Instructions

{INSTRUCTIONS}
---

## Frontier signals ({signal_count} of them)

{signals_block}

---

## Context — Finding F-001 (cross-signal pattern, raw material for noticing)

{finding_text}
"""


def build_latest(job):
    """latest.md = the job plus a results placeholder where the ~7 observations
    get filled in when the job is actually run."""
    return f"""{job}
---

## Observations

<!--
Run the job above through an LLM (or answer it yourself) and paste the
{NUM_OBSERVATIONS} observations here. Phase A success = at least one observation
the reader would not have thought of themselves (the Surprise Test).
Do NOT select a bet here — observations only.
-->

_(not yet generated — run the job above)_
"""


def build_results(observations_text, model):
    """latest.md / dated copy when the LLM HAS produced observations."""
    return f"""# Argo Observations — {TODAY}

Phase A (ARGO_V2_MIGRATION.md): observations only. No bet, no selection.
Generated by: {model}
The reusable prompt is in observation_job.md.

---

## Observations

{observations_text.strip()}

---

<!--
Surprise Test: for each observation, ask "would I have thought of this myself?"
Phase A success = at least one honest "no" that is also true.
Do NOT select a bet here — observations only.
-->
"""


SYSTEM_PROMPT = (
    "You are Argo, a frontier scout that notices what a field is paying "
    "attention to — and what it is walking past. You generate observations, "
    "never project recommendations."
)


def _guarded(provider, do_call, label):
    """Run a model call behind the daily budget, the provider circuit breaker,
    and transient-retry. Order matters: budget first (cheapest guard, hard cap),
    then breaker (fail fast if provider is down), then retry inside the breaker."""
    _BUDGET.check_and_increment()  # raises BudgetExceeded at the daily cap
    breaker = _BREAKERS.get(provider)
    run = (lambda: argo_guard.retry(do_call, label=label)) if breaker is None \
        else (lambda: breaker.call(lambda: argo_guard.retry(do_call, label=label)))
    return run()


class ModelRefusal(RuntimeError):
    """Raised by _check_refusal on a model-level refusal. A dedicated subclass
    (not a bare RuntimeError) so CircuitBreaker.call can recognize a refusal as
    a per-request content outcome and exempt it from provider-failure
    accounting (see argo_guard.CircuitBreaker.call) without string-matching the
    message."""


def _check_refusal(response, label):
    """Guard against a model-level refusal (HTTP 200, stop_reason == 'refusal',
    content empty or partial) -- seen on claude-fable-5. Every Anthropic
    response-unpack site calls this immediately after _guarded() returns and
    before touching response.content, so a refusal surfaces as a clear
    RuntimeError (ModelRefusal) instead of silently degrading to an empty
    "".join(...) (or, on a stricter unpack, an IndexError). Raising here routes
    the failure through the same `except Exception` model-failure path every
    other call-site error already takes (see argo_webhook._llm_reply's
    last_error handling) -- ModelRefusal is-a RuntimeError, so no new catch site
    is needed anywhere that only expects RuntimeError.

    A no-op for every normal stop_reason (end_turn, max_tokens, tool_use, ...),
    so this changes nothing about existing behavior."""
    if getattr(response, "stop_reason", None) == "refusal":
        log.warning("model refused: %s", label)
        raise ModelRefusal(f"model refused: {label}")


def _call_openai(job, model, temperature=1.0):
    from openai import OpenAI  # lazy: no-key path needs no SDK

    # Strip the key: a stray newline/space (common when pasting into a CI secret)
    # makes an illegal Authorization header value and surfaces as a confusing
    # "Connection error".
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())

    def do_call():
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": job},
            ],
        }
        # Omit temperature for models that reject a custom value (gpt-5/o-series
        # accept only the default); otherwise default 1.0 -- observations want breadth.
        if temperature is not None and not _rejects_temperature(model):
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    response = _guarded("openai", do_call, f"openai/{model}")
    argo_cost.record_usage(response, model, "openai", f"openai/{model}")
    return response.choices[0].message.content


def _call_xai(job, model, temperature=1.0):
    from openai import OpenAI  # lazy: xAI speaks the OpenAI chat-completions protocol

    # api.x.ai/v1 is OpenAI-chat-completions compatible -- same SDK, just a different
    # base_url + key. Strip the key (see _call_openai) to avoid an illegal
    # Authorization header from a trailing newline/space. (xAI's tool/search lives
    # behind a separate /v1/responses Agent Tools API, NOT this chat path -- see the
    # PROVIDERS row's supports_mcp=False.)
    client = OpenAI(api_key=os.environ["XAI_API_KEY"].strip(),
                    base_url="https://api.x.ai/v1")

    def do_call():
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": job},
            ],
        }
        # grok-* accepts a custom temperature (not in _TEMPERATURE_REJECTING_PREFIXES).
        if temperature is not None and not _rejects_temperature(model):
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    response = _guarded("xai", do_call, f"xai/{model}")
    argo_cost.record_usage(response, model, "xai", f"xai/{model}")
    return response.choices[0].message.content


def _call_anthropic(job, model, temperature=1.0):
    import anthropic  # lazy: no-key path needs no SDK

    # Strip the key (see _call_openai) to avoid illegal-header errors from a
    # trailing newline/space in the env value.
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

    def do_call():
        kwargs = {
            "model": model,
            "max_tokens": 1024,
            # Mark the stable system prompt with a cache breakpoint (EV-002): on
            # repeated turns the cached prefix is billed at ~10 percent. SYSTEM_PROMPT
            # here is fully stable (no timestamps), so the whole thing caches.
            "system": system_with_cache(SYSTEM_PROMPT),
            "messages": [{"role": "user", "content": job}],
        }
        # Omit temperature for models that reject a custom value (e.g. opus-4-8).
        if temperature is not None and not _rejects_temperature(model):
            kwargs["temperature"] = temperature
        return client.messages.create(**kwargs)

    response = _guarded("anthropic", do_call, f"anthropic/{model}")
    # Record usage BEFORE the refusal check: a refusal is an HTTP-200 response
    # that can still bill partial output, and response.usage is present on it --
    # recording after a raise would skip the ledger row entirely and undercount
    # spend exactly when a premium model refuses.
    argo_cost.record_usage(response, model, "anthropic", f"anthropic/{model}")
    _check_refusal(response, f"anthropic/{model}")
    return "".join(
        block.text for block in response.content if block.type == "text"
    )


def describe_image(image_bytes, media_type, prompt, model=None, system=None,
                   max_tokens=1024):
    """Send an image (+ a text prompt) to Claude's vision and return its text.

    Used when the user texts Argo a screenshot: Argo can actually SEE it and
    reason about it, instead of silently dropping it. Anthropic-only (Claude is
    multimodal); same guardrails as the other calls. `image_bytes` is raw bytes,
    `media_type` like 'image/png' or 'image/jpeg'.
    """
    import base64
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
    model = model or "claude-sonnet-4-6"
    content = [
        {"type": "image", "source": {
            "type": "base64", "media_type": media_type,
            "data": base64.b64encode(image_bytes).decode(),
        }},
        {"type": "text", "text": prompt},
    ]

    def do_call():
        kwargs = {"model": model, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": content}]}
        if system:
            kwargs["system"] = system
        return client.messages.create(**kwargs)

    response = _guarded("anthropic", do_call, f"vision/{model}")
    # Record usage before the refusal check -- see _call_anthropic for why.
    argo_cost.record_usage(response, model, "anthropic", f"vision/{model}")
    _check_refusal(response, f"vision/{model}")
    return "".join(b.text for b in response.content if b.type == "text")


# Beta header for the MCP connector (Anthropic runs the tool loop server-side).
# Isolated here so a beta-string change touches one place. Confirm against
# current Anthropic docs if connector calls start failing.
MCP_BETA = "mcp-client-2025-11-20"


# Models that reject a custom `temperature` (the API 400s) -> we OMIT the param and
# take the model default. claude-opus-4-8 and claude-fable-5 reject it outright;
# OpenAI reasoning models (gpt-5, o1/o3/o4) accept ONLY the default (1), so passing 0
# fails the same way. Matched by prefix so a dated alias (claude-opus-4-8-20xxxxxx,
# gpt-5-mini) is covered. Do NOT add gpt-4o / gpt-4.1 -- those accept a custom
# temperature (the watch judge relies on temperature=0 there).
_TEMPERATURE_REJECTING_PREFIXES = ("claude-opus-4-8", "claude-fable-5", "gpt-5",
                                   "o1", "o3", "o4")


def _rejects_temperature(model):
    return any(model.startswith(p) for p in _TEMPERATURE_REJECTING_PREFIXES)


def _record_tool_error(name, detail):
    """Feed a failed MCP tool call to the diagnose loop. Shared by both connector
    telemetry loops (Anthropic mcp_tool_result, OpenAI mcp_call) so the incident
    contract ('tool_error', 'name: detail') lives in one place. Never raises -- a
    broken ledger must not break a chat turn, but the failure is logged, not
    silently swallowed."""
    try:
        import argo_incidents
        argo_incidents.record_incident("tool_error", f"{name}: {detail}",
                                       str(detail)[:200])
    except Exception:
        log.debug("record_incident failed for tool %s", name, exc_info=True)


# Resume a paused connector tool loop at most this many times per turn. Each
# resume is another guarded API call, so this bounds a runaway loop's spend.
_MAX_PAUSE_RESUMES = 4


def _collect_tool_events(response, events):
    """Log every tool the connector fired in `response` (name on use, ok/error on
    result) and append the fired names to `events`. A bare error-only log hid the
    most important case -- the model SAYS it sent/proposed something but no tool
    fired -- so each call is logged and the caller gets the fired-tool list to
    detect that phantom (return_tool_events)."""
    name = "?"  # last tool_use name; a result block follows its own use
    for b in response.content:
        bt = getattr(b, "type", "")
        if bt == "mcp_tool_use":
            name = getattr(b, "name", "?")
            events.append(name)
            log.info("mcp tool_use: %s", name)
        elif bt == "mcp_tool_result":
            import argo_incidents  # scrub secrets before the snippet is logged/stored
            snippet = argo_incidents._redact(str(getattr(b, "content", ""))[:200])
            if getattr(b, "is_error", False):
                log.warning("mcp tool_result ERROR: %s", snippet)
                _record_tool_error(name, snippet)
            else:
                log.info("mcp tool_result ok: %s", snippet)


def _final_text(content):
    """The reply text of a (possibly tool-looping) response: only the text AFTER
    the last tool block. The connector interleaves the model's working narration
    ("Let me check the schedule... Now let me read the watch module...") between
    tool calls; joining every text block sent that inner monologue to Telegram as
    the reply. A no-tool response has no tool block, so last_tool stays -1 and the
    whole thing is returned unchanged.

    Fallback when nothing follows the last tool block (the turn ended on a tool
    call): the LAST text block only, not every text block -- re-joining all of
    them would dump the very working narration this exists to suppress."""
    texts = [b.text for b in content if getattr(b, "type", None) == "text"]
    last_tool = -1
    for i, b in enumerate(content):
        if getattr(b, "type", "") in ("mcp_tool_use", "mcp_tool_result"):
            last_tool = i
    tail = "".join(b.text for b in content[last_tool + 1:]
                   if getattr(b, "type", None) == "text")
    if tail.strip():
        return tail
    return texts[-1] if texts else ""


def chat_with_mcp(system, messages, model, mcp_servers=None, max_tokens=1024,
                  temperature=1.0, return_tool_events=False, output_schema=None):
    """Claude chat call with structured messages and optional MCP tool servers.

    Separate from generate_observations (the string-in/string-out helper the
    batch jobs use) because tool-use needs structured messages + the MCP beta.
    `messages` is a list of {role: 'user'|'assistant', content: str}. `mcp_servers`
    is the connector's server-definition list; for each server we add the matching
    `mcp_toolset` entry to `tools` (required by the 2025-11-20 connector). Anthropic
    runs the tool loop and may return mcp_tool_use/mcp_tool_result blocks alongside
    text. Returns the reply text (the segment AFTER the last tool block -- the
    interleaved working narration is not the reply; see _final_text); with
    return_tool_events=True returns (text, [fired_tool_name, ...]) so a caller can
    tell a real send from a phantom.

    Dispatches by provider: Claude via the Anthropic MCP connector (below), GPT via
    the OpenAI Responses API remote-MCP tool (_chat_with_mcp_openai). Both point at
    the SAME remote MCP server, run the tool loop server-side, and return the SAME
    (text, events) receipt -- so when the Claude brain is down the GPT fallback keeps
    real tool access, and the webhook's anti-bluff claim<->receipt gate is unchanged.
    Used by argo_webhook._llm_reply.
    """
    provider = provider_for(model)
    if provider and provider["name"] == "openai":
        return _chat_with_mcp_openai(
            system, messages, model, mcp_servers, max_tokens, temperature,
            return_tool_events)
    if not (provider and provider["name"] == "anthropic"):
        # Only Anthropic + OpenAI have an MCP tool path. A chat-only provider (e.g.
        # xai/grok, supports_mcp=False) must never fall through to the Anthropic
        # client below -- fail loudly with a clear pointer instead of a confusing
        # ANTHROPIC_API_KEY/SDK crash. (Today every caller gates on supports_mcp;
        # this guards a future one.)
        raise ValueError(
            f"chat_with_mcp has no tool path for model {model!r} "
            f"(provider {provider['name'] if provider else None}); use "
            "generate_observations for chat-completions-only providers")
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        # Mark the big stable system prompt (capabilities + self beliefs + profile)
        # with a cache breakpoint (EV-002). On repeated webhook turns the cached
        # prefix is billed at ~10 percent instead of re-billed in full. The caller
        # must keep volatile context (timestamps, reordered sections) OUT of this
        # stable string -- a churning prefix silently misses the cache.
        "system": system_with_cache(system),
        "messages": messages,
    }
    # Some newer models reject `temperature` outright (the API 400s). Omit it for
    # those, and whenever a caller passes None to take the model's default. This
    # guards every caller (incl. the webhook's Opus-escalated chat turns), not
    # just the ones that remember to pass None.
    if temperature is not None and not _rejects_temperature(model):
        kwargs["temperature"] = temperature
    if output_schema is not None:
        # Structured outputs (GA on Fable 5 / Opus 4.8 / Sonnet 4.6 / Haiku 4.5): force a
        # schema-valid JSON reply so a malformed body can't silently drop a result. The
        # caller passes a JSON Schema; it must use additionalProperties:false and no
        # numeric/length constraints or the API 400s. Anthropic-only -- the OpenAI branch
        # returned above and would need response_format instead.
        kwargs["output_config"] = {
            "format": {"type": "json_schema", "schema": output_schema}}
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
        # Each server must be referenced by exactly one mcp_toolset (2025-11-20).
        kwargs["tools"] = [
            {"type": "mcp_toolset", "mcp_server_name": s["name"]}
            for s in mcp_servers
        ]
        kwargs["betas"] = [MCP_BETA]
        do_call = lambda: client.beta.messages.create(**kwargs)
    else:
        # No tools (Phase A path): a plain messages call, no beta needed.
        do_call = lambda: client.messages.create(**kwargs)

    # Same guardrails as the other model calls: daily budget + breaker + retry.
    response = _guarded("anthropic", do_call, f"chat/{model}")
    # Record usage before the refusal check -- see _call_anthropic for why.
    argo_cost.record_usage(response, model, "anthropic", f"chat/{model}")
    _check_refusal(response, f"chat/{model}")

    events = []
    if mcp_servers:
        _collect_tool_events(response, events)
        # The connector PAUSES a long server-side tool loop (stop_reason
        # "pause_turn") and expects the caller to resume by sending the paused
        # content back as the assistant turn. Left unresumed, the model's
        # half-finished narration ("Let me propose the edit.") became the final
        # Telegram reply and the planned work silently never happened.
        #
        # Accumulate every response's content into ONE growing assistant turn:
        # the resumed responses carry only the CONTINUATION, so text emitted
        # before the pause would be lost if we looked at the last response alone,
        # and echoing each response as its own assistant message would produce
        # consecutive assistant turns. Both are avoided by resending the single
        # accumulated turn. base_messages is captured once so the caller's list
        # never grows as a side effect.
        base_messages = kwargs["messages"]
        accumulated = list(response.content)
        resumes = 0
        while (getattr(response, "stop_reason", None) == "pause_turn"
               and resumes < _MAX_PAUSE_RESUMES):
            resumes += 1
            log.info("mcp pause_turn: resuming (%d/%d)", resumes,
                     _MAX_PAUSE_RESUMES)
            kwargs["messages"] = base_messages + [
                {"role": "assistant", "content": list(accumulated)}]
            response = _guarded("anthropic", do_call, f"chat/{model}")
            # Same per-response bookkeeping as the first call: usage recorded
            # BEFORE the refusal check, and every unpack site checks refusal.
            argo_cost.record_usage(response, model, "anthropic", f"chat/{model}")
            _check_refusal(response, f"chat/{model}")
            _collect_tool_events(response, events)
            accumulated += list(response.content)
        if getattr(response, "stop_reason", None) == "pause_turn":
            # Hit the resume cap still paused: the reply may be truncated, so say
            # so in the log rather than shipping a half-finished turn silently.
            log.warning("mcp pause_turn: resume cap (%d) hit; reply may be "
                        "truncated", _MAX_PAUSE_RESUMES)
        text = _final_text(accumulated)
    else:
        text = _final_text(response.content)
    return (text, events) if return_tool_events else text


# Reasoning models (gpt-5*) spend tokens on hidden reasoning that counts against
# max_output_tokens, so the chat default (1024) can be fully consumed before any
# visible text -- the turn returns empty. Floor the Responses budget so a fallback
# answer has room. (A cap, not a target -- billed only for tokens actually produced.)
_OPENAI_MIN_OUTPUT_TOKENS = 4096


def _chat_with_mcp_openai(system, messages, model, mcp_servers, max_tokens,
                          temperature, return_tool_events):
    """GPT counterpart to chat_with_mcp: the OpenAI Responses API talking to the
    SAME remote MCP server (Streamable HTTP) the Anthropic connector uses. OpenAI
    runs the tool loop; each fired tool surfaces as an `mcp_call` output item, which
    we collect as the receipt (same contract as the Anthropic path). `mcp_servers`
    entries are the connector shape built by argo_webhook._build_mcp_servers
    (name/url/authorization_token); None -> a plain tool-less Responses call."""
    from openai import OpenAI  # lazy: no-key path needs no SDK

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())
    tools = [
        {
            "type": "mcp",
            "server_label": s["name"],
            "server_url": s["url"],
            # The MCP server is our own bearer-gated, allowlist-enforced endpoint, so
            # auto-approve calls -- there's no human in the loop on a chat turn.
            "require_approval": "never",
            "headers": {"Authorization": f"Bearer {s['authorization_token']}"},
        }
        for s in (mcp_servers or [])
    ]

    def do_call():
        kwargs = {
            "model": model,
            "instructions": system,
            "input": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_output_tokens": max(max_tokens, _OPENAI_MIN_OUTPUT_TOKENS),
        }
        if tools:
            kwargs["tools"] = tools
        # gpt-5/o-series reject a custom temperature (accept only the default).
        if temperature is not None and not _rejects_temperature(model):
            kwargs["temperature"] = temperature
        return client.responses.create(**kwargs)

    response = _guarded("openai", do_call, f"responses/{model}")
    argo_cost.record_usage(response, model, "openai", f"responses/{model}")

    # Receipt: each tool the connector fired is an `mcp_call` output item. Only a
    # SUCCEEDED call counts toward the receipt -- a failed propose_change must not
    # back an "I opened a PR" claim (the phantom-claim gate keys off this list), so an
    # errored call is logged + fed to the diagnose loop but kept OUT of `events`.
    events = []
    for item in (getattr(response, "output", None) or []):
        if getattr(item, "type", "") != "mcp_call":
            continue
        name = getattr(item, "name", "?")
        err = getattr(item, "error", None)
        if err:
            log.warning("openai mcp_call ERROR: %s", str(err)[:200])
            _record_tool_error(name, err)
        else:
            events.append(name)
            log.info("openai mcp_call ok: %s", name)

    # Mirror the Anthropic path's final-segment rule (_final_text): message text
    # emitted BEFORE the last tool call is working narration, not the reply.
    # Fall back to the full walk, then to output_text (the SDK's aggregate),
    # when the tail is empty.
    items = list(getattr(response, "output", None) or [])
    last_call = max((i for i, item in enumerate(items)
                     if getattr(item, "type", "") == "mcp_call"), default=-1)

    def _message_text(seq):
        chunks = []
        for item in seq:
            if getattr(item, "type", "") != "message":
                continue
            for c in (getattr(item, "content", None) or []):
                t = getattr(c, "text", None)
                if t:
                    chunks.append(t)
        return "".join(chunks)

    text = _message_text(items[last_call + 1:])
    if not text.strip():
        text = _message_text(items) or (getattr(response, "output_text", None) or "")
    return (text, events) if return_tool_events else text


# Provider registry. Each entry: how to recognise a model name, which env var
# holds its key, and how to call it. Adding a provider = adding a row here; no
# provider is hardcoded into the generation flow.
PROVIDERS = (
    {
        "name": "openai",
        "key_env": "OPENAI_API_KEY",
        "matches": lambda m: m.startswith(("gpt-", "o1", "o3", "o4")),
        "call": _call_openai,
        "supports_mcp": True,  # via the OpenAI Responses remote-MCP connector
    },
    {
        "name": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "matches": lambda m: m.startswith("claude"),
        "call": _call_anthropic,
        "supports_mcp": True,  # via the Anthropic MCP connector
    },
    {
        "name": "xai",
        "key_env": "XAI_API_KEY",
        "matches": lambda m: m.startswith("grok"),
        "call": _call_xai,
        # Chat only: xAI's tools are a separate /v1/responses Agent Tools API, NOT
        # the OpenAI-Responses remote-MCP path _chat_with_mcp_openai uses. False
        # keeps grok off every tool loop (dispatch checks name=='openai'; the
        # webhook's tool path is gated by supports_mcp).
        "supports_mcp": False,
    },
)


def provider_for(model):
    """Route a model name to its provider entry, or None if unrecognised."""
    for provider in PROVIDERS:
        if provider["matches"](model):
            return provider
    return None


def supports_mcp(model):
    """True if the model's provider can run the MCP tool loop (Anthropic connector
    or OpenAI Responses). Drives whether a chat turn takes the structured tool path,
    so the webhook never hardcodes provider names. Registry-driven: a new tool-capable
    provider just sets supports_mcp=True on its row."""
    p = provider_for(model)
    return bool(p and p.get("supports_mcp"))


def resolve_models():
    """Models to try, in order. ARGO_MODEL (any provider) wins if set."""
    override = os.environ.get("ARGO_MODEL")
    if override:
        return [override]
    return list(DEFAULT_MODELS)


def generate_observations(job, model, temperature=1.0):
    """Generate observations for `model`, routing to the right provider.

    Raises on a missing key, unknown provider, or API/SDK error so the caller
    can fall back to job-only. No observations are ever fabricated here.
    `temperature` defaults to 1.0 (breadth); pass 0 for a deterministic verdict
    (the watch judge uses this so the same items don't flip run-to-run).
    """
    provider = provider_for(model)
    if provider is None:
        raise RuntimeError(
            f"no known provider for model '{model}' "
            "(expected gpt-*/o* OpenAI, claude-* Anthropic, or grok-* xAI)"
        )
    if not os.environ.get(provider["key_env"]):
        raise RuntimeError(
            f"{provider['key_env']} not set for model '{model}' "
            f"({provider['name']})"
        )
    return provider["call"](job, model, temperature)


def main():
    if not SIGNALS_PATH.exists():
        raise SystemExit(f"Missing input: {SIGNALS_PATH.relative_to(ROOT)}")
    if not FINDING_PATH.exists():
        raise SystemExit(f"Missing input: {FINDING_PATH.relative_to(ROOT)}")

    signals = load_signals()
    signals_block = format_signals(signals)
    finding_text = FINDING_PATH.read_text().strip()

    job = build_job(signals_block, len(signals), finding_text)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    job_path = OUT_DIR / "observation_job.md"
    latest_path = OUT_DIR / "latest.md"
    dated_path = OUT_DIR / f"{TODAY}.md"

    # Always write the reusable job (works with or without a key).
    job_path.write_text(job)

    print("\n🧭 Argo — Observe (Phase A)\n")
    print(f"Loaded {len(signals)} signals + F-001 as context:")
    for s in signals:
        print(f"  • {s['title']}")
    print()
    print(f"Wrote reusable job: {job_path.relative_to(ROOT)}")

    models = resolve_models()

    # Which of the models we'd try actually have a provider + key available?
    runnable = [
        m for m in models
        if (p := provider_for(m)) and os.environ.get(p["key_env"])
    ]

    # ---- No usable key: job-only fallback, do not crash, do not fabricate. ----
    if not runnable:
        latest_path.write_text(build_latest(job))
        needed = sorted({
            p["key_env"]
            for m in models if (p := provider_for(m))
        })
        print()
        print("No API key available for the selected model(s): "
              + ", ".join(models))
        if needed:
            print("Set one of: " + ", ".join(needed)
                  + " (in .env or the environment).")
        print(f"Left {latest_path.relative_to(ROOT)} as a placeholder.")
        print("See docs/ARGO_LLM_SETUP.md.")
        print("\n⚠️  No observations generated (no model is configured to run).\n")
        return

    # ---- Key present: call the LLM, try runnable models in order. ----
    observations_text = None
    used_model = None
    errors = []
    for model in runnable:
        try:
            observations_text = generate_observations(job, model)
            used_model = model
            break
        except Exception as exc:  # API error, bad model, SDK missing, etc.
            errors.append(f"{model}: {exc}")

    if observations_text is None:
        latest_path.write_text(build_latest(job))
        print()
        print("All model calls failed:")
        for e in errors:
            print(f"  - {e}")
        print(f"Left {latest_path.relative_to(ROOT)} as a placeholder.")
        print("\n⚠️  No observations generated (all model calls failed).\n")
        return

    results = build_results(observations_text, used_model)
    latest_path.write_text(results)
    dated_path.write_text(results)
    print()
    print(f"Generated observations via {used_model}.")
    print(f"Wrote {latest_path.relative_to(ROOT)} and "
          f"{dated_path.relative_to(ROOT)}.")
    print("\n✅ Observations generated.\n")


if __name__ == "__main__":
    main()
