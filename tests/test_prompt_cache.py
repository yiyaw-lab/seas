"""
EV-002: Anthropic prompt caching for the stable system prompt.

These tests fail on the pre-fix code (chat_with_mcp passes `system` straight
through with no cache_control) and pass once the stable prefix is marked with a
cache breakpoint.
"""

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _install_fake_anthropic(capture):
    """Install a fake `anthropic` module that records the kwargs of the call."""
    fake = types.ModuleType("anthropic")

    class _Block:
        type = "text"
        text = "ok"

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            capture["kwargs"] = kwargs
            return _Resp()

    class _Anthropic:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    fake.Anthropic = _Anthropic
    sys.modules["anthropic"] = fake
    return fake


def test_system_with_cache_marks_stable_prefix():
    from argo_observe_cache_patch import system_with_cache

    out = system_with_cache("big stable prompt")
    assert isinstance(out, list) and len(out) == 1
    assert out[0]["type"] == "text"
    assert out[0]["text"] == "big stable prompt"
    assert out[0]["cache_control"] == {"type": "ephemeral"}


def test_system_with_cache_passthrough_for_none_and_list():
    from argo_observe_cache_patch import system_with_cache

    assert system_with_cache(None) is None
    assert system_with_cache("") == ""
    already = [{"type": "text", "text": "x"}]
    assert system_with_cache(already) is already


def test_chat_with_mcp_sends_cache_control(monkeypatch):
    capture = {}
    _install_fake_anthropic(capture)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    import argo_observe

    # Bypass the real guardrails (budget/breaker) so we exercise just the call.
    monkeypatch.setattr(argo_observe, "_guarded",
                        lambda provider, do_call, label: do_call())

    text = argo_observe.chat_with_mcp(
        system="STABLE capabilities + self beliefs + profile",
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
    )
    assert text == "ok"

    sent = capture["kwargs"]["system"]
    # The stable prefix must be sent as structured blocks with a cache breakpoint.
    assert isinstance(sent, list)
    assert sent[0]["cache_control"] == {"type": "ephemeral"}
    assert sent[0]["text"] == "STABLE capabilities + self beliefs + profile"
