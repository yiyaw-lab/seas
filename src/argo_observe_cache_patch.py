"""
Prompt-caching helper for Argo's Anthropic chat calls (EV-002).

The big, stable system prompt (capabilities + self beliefs + profile) is
re-billed on every webhook turn today. Anthropic supports prompt caching:
mark a stable prefix with a `cache_control` breakpoint and the cached tokens
are billed at ~10 percent on subsequent turns within the cache window.

This module provides one small, pure function -- `system_with_cache` -- that
converts a plain `system` string into Anthropic's structured-blocks form with a
`cache_control` breakpoint on the stable prefix, leaving volatile context AFTER
the breakpoint. It is wired into argo_observe.chat_with_mcp so the Anthropic
branch sends a cacheable system prompt.

Why a separate file: the bug is a recurring one (a churning prefix silently
misses the cache), so the cache rule lives in exactly one tested place and the
call site just calls it.

Contract:
  - Given a non-empty string, return a single-element list of one text block
    carrying `cache_control: {type: 'ephemeral'}`. This is the stable prefix --
    callers must NOT fold timestamps/reordered sections into it.
  - Given an already-structured list (someone pre-split stable vs volatile),
    return it unchanged so volatile blocks stay after the breakpoint.
  - Given None/empty, return it unchanged (nothing to cache).
"""


def system_with_cache(system):
    """Return an Anthropic `system` value with a cache breakpoint on the stable
    prefix.

    A plain string is treated as the stable prefix and wrapped in one text block
    carrying cache_control. A list is assumed already split (stable blocks first,
    volatile last) and returned unchanged. None/empty is returned unchanged.
    """
    if not system:
        return system
    if isinstance(system, str):
        return [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
    # Already structured: trust the caller's stable/volatile ordering.
    return system
