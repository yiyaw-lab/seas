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
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import argo_incidents as inc
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

    def test_mcp_tool_result_error_snippet_is_redacted_before_logging(self):
        """Secrets in mcp_tool_result content must be redacted before the snippet is
        logged or recorded -- the raw error body can embed a bearer token or email.

        FAILS before the chat_with_mcp mcp_tool_result snippet-redact fix.
        """
        tmp = tempfile.mkdtemp()
        with mock.patch.object(inc, "INCIDENTS_PATH", Path(tmp) / "inc.json"):
            with mock.patch.object(observe, "_record_tool_error", lambda *a: None):
                blocks = [
                    _block(type="mcp_tool_use", name="github_read_file"),
                    _block(type="mcp_tool_result", is_error=True,
                           content="401: Authorization: Bearer ghp_SECRETTOKEN1234567 ops@example.com"),
                ]
                with self.assertLogs("argo_observe", level="WARNING") as cm:
                    self._run(blocks)
        logged = " ".join(cm.output)
        self.assertNotIn("ghp_SECRETTOKEN1234567", logged)
        self.assertNotIn("ops@example.com", logged)
        self.assertIn("<redacted>", logged)

    def test_narration_before_tools_is_dropped(self):
        # The reply is the segment AFTER the last tool block; the interleaved
        # working narration ("Let me check...") used to be joined into the
        # Telegram reply as leaked inner monologue.
        text, events = self._run(
            [_block(type="text", text="Let me check the schedule first."),
             _block(type="mcp_tool_use", name="web_fetch"),
             _block(type="mcp_tool_result", is_error=False, content="ok"),
             _block(type="text", text="Here is the answer.")],
            return_tool_events=True)
        self.assertEqual(text, "Here is the answer.")
        self.assertEqual(events, ["web_fetch"])

    def test_all_narration_falls_back_to_full_text(self):
        # Nothing after the last tool block: better the narration than nothing
        # (an empty reply reads as a model failure upstream).
        text, _ = self._run(
            [_block(type="text", text="Let me check."),
             _block(type="mcp_tool_use", name="web_fetch"),
             _block(type="mcp_tool_result", is_error=False, content="ok")],
            return_tool_events=True)
        self.assertEqual(text, "Let me check.")

    def test_pause_turn_is_resumed_and_events_merged(self):
        # The connector pauses a long tool loop (stop_reason "pause_turn");
        # unresumed, the half-finished narration became the final reply and the
        # planned work silently never happened.
        paused = _response(
            [_block(type="text", text="Let me look."),
             _block(type="mcp_tool_use", name="web_fetch"),
             _block(type="mcp_tool_result", is_error=False, content="ok")])
        paused.stop_reason = "pause_turn"
        final = _response(
            [_block(type="mcp_tool_use", name="propose_change"),
             _block(type="mcp_tool_result", is_error=False, content="ok"),
             _block(type="text", text="Opened the PR.")])
        responses = [paused, final]
        calls = []

        def guarded(provider, fn, label):
            calls.append(label)
            return responses.pop(0)

        with mock.patch.object(observe, "_guarded", guarded):
            text, events = observe.chat_with_mcp(
                "sys", _MESSAGES, "claude-sonnet-4-6", mcp_servers=_SERVERS,
                return_tool_events=True)
        self.assertEqual(text, "Opened the PR.")
        self.assertEqual(events, ["web_fetch", "propose_change"])
        self.assertEqual(len(calls), 2)  # one resume, no runaway loop

    def test_pre_pause_answer_survives_a_resume(self):
        # The answer is emitted BEFORE the pause; the resumed response carries
        # only a tool call + a short ack. Because content is accumulated across
        # responses, the earlier answer is not lost.
        paused = _response(
            [_block(type="text", text="The answer is 42."),
             _block(type="mcp_tool_use", name="web_fetch"),
             _block(type="mcp_tool_result", is_error=False, content="ok")])
        paused.stop_reason = "pause_turn"
        final = _response(
            [_block(type="mcp_tool_use", name="verify_feed"),
             _block(type="mcp_tool_result", is_error=False, content="ok")])
        # final ends on a tool block with no trailing text -> the accumulated
        # tail is empty, so _final_text falls back to the last text block, which
        # is the real answer emitted before the pause.
        responses = [paused, final]
        with mock.patch.object(
                observe, "_guarded",
                lambda provider, fn, label: responses.pop(0)):
            text, events = observe.chat_with_mcp(
                "sys", _MESSAGES, "claude-sonnet-4-6", mcp_servers=_SERVERS,
                return_tool_events=True)
        self.assertEqual(text, "The answer is 42.")
        self.assertEqual(events, ["web_fetch", "verify_feed"])

    def test_two_resumes_complete_and_do_not_mutate_caller_messages(self):
        # Two consecutive pauses (each a real tool-loop chunk): the loop resumes
        # twice, the reply is the text after the LAST tool block across the whole
        # accumulation, and the caller's messages list is never grown as a side
        # effect (base_messages captured once; each resend is a fresh list).
        def chunk(narration, tool, stop):
            r = _response([_block(type="text", text=narration),
                           _block(type="mcp_tool_use", name=tool),
                           _block(type="mcp_tool_result", is_error=False,
                                  content="ok")])
            r.stop_reason = stop
            return r

        r1 = chunk("looking", "web_fetch", "pause_turn")
        r2 = chunk("still going", "verify_feed", "pause_turn")
        r3 = _response([_block(type="mcp_tool_use", name="github_list"),
                        _block(type="mcp_tool_result", is_error=False, content="ok"),
                        _block(type="text", text="done")])
        r3.stop_reason = "end_turn"
        responses = [r1, r2, r3]
        caller_messages = [{"role": "user", "content": "go"}]
        with mock.patch.object(
                observe, "_guarded",
                lambda provider, fn, label: responses.pop(0)):
            text, events = observe.chat_with_mcp(
                "sys", caller_messages, "claude-sonnet-4-6",
                mcp_servers=_SERVERS, return_tool_events=True)
        self.assertEqual(text, "done")  # tail after the last tool, not narration
        self.assertEqual(events, ["web_fetch", "verify_feed", "github_list"])
        self.assertEqual(caller_messages, [{"role": "user", "content": "go"}])


if __name__ == "__main__":
    unittest.main()
