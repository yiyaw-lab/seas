"""SEAS signal scoring — the 5-dimension LLM scorer, in two paths.

Extracted from seas_finding (which crossed the 500-line target when the batch
path landed): this is the cohesive scoring seam. Callers import
`seas_scoring.auto_score_signals` directly — seas_finding does NOT re-export it
(that would reintroduce the cycle this split avoids).

Two paths, ONE scoring contract:
  - DEFAULT (batch=False): the original one-Claude-call-per-unscored-signal loop.
    Zero behavior change.
  - OPT-IN (batch=True): collect the unscored signals into ONE Message Batches
    call (argo_batch.run_batch, 50% off). dry_run assembles + reports only — no
    live call, nothing spent — the rehearse gate.

The shared scoring primitives (`SYSTEM`, `_extract_json`, `SCORE_PROMPT_PATH`)
stay owned by seas_finding — the synthesis and benchmark paths use them too — so
we import them rather than duplicate. The 5-dimension parse/validate contract
lives in `_apply_scores` here so the single-call and batch paths never drift.
"""

import argo_observe as observe
from seas_finding import SCORE_PROMPT_PATH, SYSTEM, _extract_json


def _is_unscored(sig):
    """A signal is unscored when every dimension is still 0 (the fetch default)."""
    return all(v == 0 for v in sig.get("scores", {}).values())


def _score_prompt(prompt_template, sig):
    """The per-signal user prompt — identical text in the single-call and batch
    paths so the model sees the same job either way."""
    return (f"{prompt_template}\n\nSignal to score:\n"
            f"Title: {sig['title']}\n"
            f"Summary: {sig.get('summary', '')}")


def _apply_scores(sig, reply):
    """Parse a model reply into a scored signal, or return None if the reply is
    unusable. The contract (5 int dimensions, durability+leverage int-validated)
    is the SAME in both the single-call loop and the batch path — keep it here so
    the two never drift."""
    scored = _extract_json(reply)
    if not (scored
            and isinstance(scored.get("durability"), int)
            and isinstance(scored.get("leverage"), int)):
        return None
    sig = dict(sig)
    sig["scores"] = {
        "durability":    int(scored.get("durability", 0)),
        "leverage":      int(scored.get("leverage", 0)),
        "alignment":     int(scored.get("alignment", 0)),
        "accessibility": int(scored.get("accessibility", 0)),
        "novelty":       int(scored.get("novelty", 0)),
    }
    return sig


def _scored_line(sig):
    return (f"  scored: {sig['title'][:55]} "
            f"(d={sig['scores']['durability']} "
            f"l={sig['scores']['leverage']} "
            f"a={sig['scores']['alignment']})")


def _resolve_score_model():
    """Pick the scoring model (ARGO_SEAS_MODEL wins, else the resolve_models()
    fallback), keyed-availability filtered. Returns None if none is usable."""
    import os
    # SEAS research can run a different (e.g. stronger) model than Argo's ARGO_MODEL
    # fallback: ARGO_SEAS_MODEL wins when set, else resolve_models() (ARGO_MODEL).
    # Falsy-guard so an unset/empty env never injects None/"" as a model.
    preferred = os.environ.get("ARGO_SEAS_MODEL")
    models = [m for m in (([preferred] if preferred else []) + observe.resolve_models())
              if (p := observe.provider_for(m)) and os.environ.get(p["key_env"])]
    return models[0] if models else None  # cheapest available (Sonnet by default)


def auto_score_signals(signals, dry_run=False, batch=False):
    """LLM-score any unscored signals using prompts/score_signal.md (5 dimensions).
    Returns the updated list. Best-effort: silently skips on any model/parse failure
    so a scoring hiccup never blocks the investigation step.

    `batch=False` (DEFAULT) keeps the original one-Claude-call-per-signal loop —
    zero behavior change. `batch=True` routes the unscored signals through
    argo_batch.run_batch (the Message Batches API, 50% off) in ONE asynchronous
    call instead. With `dry_run=True` the batch path only ASSEMBLES and reports
    what would be sent — no live call, nothing spent — so it can be rehearsed
    before committing the cost (ROADMAP: "Batch API for SEAS scoring, rehearse
    -gated")."""
    if not SCORE_PROMPT_PATH.exists():
        return signals
    prompt_template = SCORE_PROMPT_PATH.read_text()

    model = _resolve_score_model()
    if model is None:
        print("  (no model available for scoring — skipping)")
        return signals

    if batch:
        return _batch_score_signals(signals, prompt_template, model, dry_run)

    updated = []
    for sig in signals:
        if not _is_unscored(sig):
            updated.append(sig)
            continue
        if dry_run:
            print(f"  [dry-run] would score: {sig['title'][:60]}")
            updated.append(sig)
            continue
        prompt = _score_prompt(prompt_template, sig)
        try:
            reply = observe.generate_observations(f"{SYSTEM}\n\n{prompt}", model)
            rescored = _apply_scores(sig, reply)
            if rescored is not None:
                sig = rescored
                print(_scored_line(sig))
        except Exception as exc:
            print(f"  ! scoring failed for '{sig['title'][:40]}': {exc}")
        updated.append(sig)
    return updated


def _batch_score_signals(signals, prompt_template, model, dry_run):
    """OPT-IN bulk scoring via the Message Batches API (argo_batch.run_batch).

    Collects the unscored signals into one batch, scores them in a single
    asynchronous call at 50% cost, and maps results back BY custom_id (never by
    position). custom_ids are stable index keys (sig-<i>) into an explicit
    id->signal map, so a result can only ever update the signal it scored. A
    per-item batch failure (errored/expired/parse-miss) is SURFACED and that
    signal is left unscored — same outcome as a per-item failure in the loop,
    never silently dropped. dry_run assembles + reports only; no live call."""
    import argo_batch

    # Build the batch over the unscored signals, keyed by stable index ids. The
    # map is the source of truth for result->signal; results may return in any
    # order, so we look up by custom_id, not position.
    by_id = {}
    items = []
    for idx, sig in enumerate(signals):
        if not _is_unscored(sig):
            continue
        cid = f"sig-{idx}"
        by_id[cid] = sig
        items.append((cid, _score_prompt(prompt_template, sig)))

    if dry_run:
        print(f"  [dry-run] would batch {len(items)} unscored signal(s) "
              f"into ONE {model} call (Batches API, 50% off):")
        for cid, _ in items:
            print(f"    [dry-run] {cid}: {by_id[cid]['title'][:60]}")
        return signals

    if not items:
        return signals

    try:
        results = argo_batch.run_batch(items, model, system=SYSTEM)
    except Exception as exc:
        # A batch-level failure (create/poll/timeout) leaves every signal
        # unscored, exactly as a model outage would in the single-call loop.
        print(f"  ! batch scoring failed ({len(items)} signal(s) unscored): {exc}")
        return signals

    # Apply results back onto a fresh list, keyed by custom_id. Each id maps to
    # exactly one signal in by_id; a missing id or an ok=False result leaves that
    # signal unscored and is surfaced, not dropped.
    scored_by_id = {}
    for cid, item in by_id.items():
        res = results.get(cid)
        if res is None:
            print(f"  ! batch returned no result for {cid} "
                  f"('{item['title'][:40]}') — left unscored")
            continue
        if not res.ok:
            print(f"  ! batch item {cid} failed ({res.status}: {res.error}) — "
                  f"'{item['title'][:40]}' left unscored")
            continue
        rescored = _apply_scores(item, res.text or "")
        if rescored is None:
            print(f"  ! batch reply for {cid} unparseable — "
                  f"'{item['title'][:40]}' left unscored")
            continue
        scored_by_id[cid] = rescored
        print(_scored_line(rescored))

    # Rebuild the full list: swap in the batch-scored version where we have one,
    # otherwise keep the original signal untouched.
    updated = []
    for idx, sig in enumerate(signals):
        updated.append(scored_by_id.get(f"sig-{idx}", sig))
    return updated
