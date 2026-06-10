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

import argo_guard
from argo_log import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = ROOT / "data" / "signals.json"
FINDING_PATH = ROOT / "findings" / "F-001-cognitive-operators.md"
OUT_DIR = ROOT / "argo" / "observations"

# Resilience guardrails (Phase E1): a circuit breaker per provider + a global
# daily call budget. Wrap every model call so transient failures retry, a dead
# provider fails fast, and a runaway loop can't blow past the daily cap.
_BREAKERS = {
    "openai": argo_guard.CircuitBreaker("openai"),
    "anthropic": argo_guard.CircuitBreaker("anthropic"),
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
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": job}],
        }
        # Omit temperature for models that reject a custom value (e.g. opus-4-8).
        if temperature is not None and not _rejects_temperature(model):
            kwargs["temperature"] = temperature
        return client.messages.create(**kwargs)

    response = _guarded("anthropic", do_call, f"anthropic/{model}")
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
    return "".join(b.text for b in response.content if b.type == "text")


# Beta header for the MCP connector (Anthropic runs the tool loop server-side).
# Isolated here so a beta-string change touches one place. Confirm against
# current Anthropic docs if connector calls start failing.
MCP_BETA = "mcp-client-2025-11-20"


# Models that reject a custom `temperature` (the API 400s) -> we OMIT the param and
# take the model default. claude-opus-4-8 rejects it outright; OpenAI reasoning models
# (gpt-5, o1/o3/o4) accept ONLY the default (1), so passing 0 fails the same way.
# Matched by prefix so a dated alias (claude-opus-4-8-20xxxxxx, gpt-5-mini) is covered.
# Do NOT add gpt-4o / gpt-4.1 -- those accept a custom temperature (the watch judge
# relies on temperature=0 there).
_TEMPERATURE_REJECTING_PREFIXES = ("claude-opus-4-8", "gpt-5", "o1", "o3", "o4")


def _rejects_temperature(model):
    return any(model.startswith(p) for p in _TEMPERATURE_REJECTING_PREFIXES)


def chat_with_mcp(system, messages, model, mcp_servers=None, max_tokens=1024,
                  temperature=1.0, return_tool_events=False):
    """Claude chat call with structured messages and optional MCP tool servers.

    Separate from generate_observations (the string-in/string-out helper the
    batch jobs use) because tool-use needs structured messages + the MCP beta.
    `messages` is a list of {role: 'user'|'assistant', content: str}. `mcp_servers`
    is the connector's server-definition list; for each server we add the matching
    `mcp_toolset` entry to `tools` (required by the 2025-11-20 connector). Anthropic
    runs the tool loop and may return mcp_tool_use/mcp_tool_result blocks alongside
    text. Returns the joined text; with return_tool_events=True returns
    (text, [fired_tool_name, ...]) so a caller can tell a real send from a phantom.
    Used by argo_webhook._llm_reply. Claude-only.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    # Some newer models reject `temperature` outright (the API 400s). Omit it for
    # those, and whenever a caller passes None to take the model's default. This
    # guards every caller (incl. the webhook's Opus-escalated chat turns), not
    # just the ones that remember to pass None.
    if temperature is not None and not _rejects_temperature(model):
        kwargs["temperature"] = temperature
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

    # Telemetry: log every tool the connector fired (name on use, ok/error on
    # result) and collect the fired names. A bare error-only log hid the most
    # important case -- the model SAYS it sent/proposed something but no tool
    # fired -- so we log each call and hand the caller the fired-tool list to
    # detect that phantom (return_tool_events).
    events = []
    if mcp_servers:
        for b in response.content:
            bt = getattr(b, "type", "")
            if bt == "mcp_tool_use":
                name = getattr(b, "name", "?")
                events.append(name)
                log.info("mcp tool_use: %s", name)
            elif bt == "mcp_tool_result":
                snippet = str(getattr(b, "content", ""))[:200]
                if getattr(b, "is_error", False):
                    log.warning("mcp tool_result ERROR: %s", snippet)
                else:
                    log.info("mcp tool_result ok: %s", snippet)

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    )
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
    },
    {
        "name": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "matches": lambda m: m.startswith("claude"),
        "call": _call_anthropic,
    },
)


def provider_for(model):
    """Route a model name to its provider entry, or None if unrecognised."""
    for provider in PROVIDERS:
        if provider["matches"](model):
            return provider
    return None


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
            "(expected gpt-*/o* for OpenAI or claude-* for Anthropic)"
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
        print("\n✅ Observation job ready (no observations generated).\n")
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
        # Could not generate — fall back to placeholder rather than crash.
        latest_path.write_text(build_latest(job))
        print()
        print("A key was available, but no model call succeeded:")
        for err in errors:
            print(f"  - {err}")
        print(f"Left {latest_path.relative_to(ROOT)} as a placeholder.")
        print("\n⚠️  No observations generated (see errors above).\n")
        return

    results = build_results(observations_text, used_model)
    latest_path.write_text(results)
    dated_path.write_text(results)

    print(f"Generated observations with: {used_model}")
    print(f"Wrote: {latest_path.relative_to(ROOT)}")
    print(f"Wrote: {dated_path.relative_to(ROOT)}")
    print("\n--- Observations ---\n")
    print(observations_text.strip())
    print("\n✅ Observations generated.\n")


if __name__ == "__main__":
    main()
