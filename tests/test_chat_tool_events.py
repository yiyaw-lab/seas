"""chat_with_mcp tool-event telemetry tests (argo_observe).

Locks the backward-compatible contract that powers the phantom-send backstop and
the per-tool-call logging: the function returns a plain string by default (so the
six existing callers are untouched), and returns (text, [fired_tool_name, ...])
when return_tool_events=True.

Pure: the Anthropic client is faked via sys.modules and the guarded API call is
stubbed to return a canned response, so no network/SDK/key is needed.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from unittest import mock

import argo_observe as observe


def _block(**kw):
    return types.SimpleNamespace(**kw)


def _response(blocks):
    return types.SimpleNamespace(content=blocks)


# A fake `anthropic` module so the in-function `import anthropic` + client
# construction works without the SDK installed (the guarded call is stubbed, so
# the client is never actually used).
_FAKE_ANTHROPIC = types.SimpleNamespace(
    Anthropic=lambda api_key=None: types.SimpleNamespace())

_SERVERS = [{"name": "argo", "url": "https://example/mcp"}]
_MESSAGES = [{"role": "user", "content": "go"}]


class ChatToolEventsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}))
        self.enterContext(mock.patch.dict(sys.modules, {"anthropic": _FAKE_ANTHROPIC}))

    def _run(self, blocks, **kw):
        resp = _response(blocks)
        with mock.patch.object(observe, "_guarded", lambda provider, fn, label: resp):
            return observe.chat_with_mcp(
                "sys", _MESSAGES, "claude-sonnet-4-6", mcp_servers=_SERVERS, **kw)

    def test_returns_str_by_default(self):
        # Locks backward-compat: the six existing callers read a str.
        out = self._run([_block(type="text", text="hi")])
        self.assertIsInstance(out, str)
        self.assertEqual(out, "hi")

    def test_returns_events_when_requested(self):
        out = self._run(
            [_block(type="mcp_tool_use", name="new_project"),
             _block(type="mcp_tool_result", is_error=False, content="ok"),
             _block(type="text", text="sent.")],
            return_tool_events=True)
        self.assertEqual(out, ("sent.", ["new_project"]))

    def test_no_tools_fired_yields_empty_events(self):
        # The phantom case: model produced text but called no tool.
        text, events = self._run(
            [_block(type="text", text="sending the proposal now.")],
            return_tool_events=True)
        self.assertEqual(text, "sending the proposal now.")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
