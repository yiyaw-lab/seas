"""OpenAI remote-MCP fallback: when the Claude brain is down, GPT answers via the
OpenAI Responses API pointed at the SAME remote MCP server, so it keeps real tool
access instead of degrading to a tool-less, bluff-prone completion.

Two layers:
- argo_observe.chat_with_mcp dispatches gpt-* to _chat_with_mcp_openai, which builds
  the Responses `mcp` tool (bearer header) and parses `mcp_call` output items into the
  same (text, [fired_tool,...]) receipt the Anthropic path returns.
- argo_webhook._generate_reply routes GPT through that tool path when an MCP server is
  configured, so no "tools are down" notice fires and the claim<->receipt gate holds.

Pure + hermetic: the openai SDK is faked via sys.modules; _guarded is bypassed so no
budget/breaker/network is touched. Run from the repo root:
  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import argo_observe as observe
import argo_webhook as wh


def _fake_openai_module(capture=None, output=None, output_text=None):
    """A stand-in `openai` module whose client records the create() kwargs and
    returns a canned Responses object. Defaults to one successful propose_change
    call plus a message; pass `output`/`output_text` to model other shapes."""
    if output is None:
        output = [
            SimpleNamespace(type="mcp_call", name="propose_change", error=None),
            SimpleNamespace(type="message", content=[
                SimpleNamespace(text="Opened the PR for review: link")]),
        ]
        output_text = "Opened the PR for review: link"
    mod = types.ModuleType("openai")

    class _Responses:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return SimpleNamespace(output=output, output_text=output_text)

    class _Client:
        def __init__(self, **_):
            self.responses = _Responses()

    mod.OpenAI = _Client
    return mod


class OpenAiMcpChatTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}))
        # Bypass budget/breaker/retry: just run the call.
        self.enterContext(mock.patch.object(
            observe, "_guarded", lambda provider, do_call, label: do_call()))

    def test_dispatches_gpt_to_responses_and_parses_receipt(self):
        self.enterContext(mock.patch.dict(sys.modules, {"openai": _fake_openai_module()}))
        servers = [{"name": "argo", "url": "https://x/mcp/mcp",
                    "authorization_token": "tok"}]
        text, events = observe.chat_with_mcp(
            "SYS", [{"role": "user", "content": "open a PR"}], "gpt-5.5",
            mcp_servers=servers, return_tool_events=True)
        self.assertEqual(text, "Opened the PR for review: link")
        self.assertEqual(events, ["propose_change"])  # mcp_call -> receipt

    def test_builds_mcp_tool_with_bearer_header(self):
        cap = {}
        self.enterContext(mock.patch.dict(sys.modules, {"openai": _fake_openai_module(cap)}))
        servers = [{"name": "argo", "url": "https://x/mcp/mcp",
                    "authorization_token": "tok"}]
        observe.chat_with_mcp("SYS", [{"role": "user", "content": "hi"}], "gpt-5.5",
                              mcp_servers=servers, return_tool_events=True)
        self.assertEqual(cap["model"], "gpt-5.5")
        self.assertEqual(cap["instructions"], "SYS")
        tool = cap["tools"][0]
        self.assertEqual(tool["type"], "mcp")
        self.assertEqual(tool["server_url"], "https://x/mcp/mcp")
        self.assertEqual(tool["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(tool["require_approval"], "never")
        # gpt-5* rejects a custom temperature -> it must be omitted.
        self.assertNotIn("temperature", cap)
        # Reasoning headroom: the output budget is floored so reasoning can't starve
        # the visible reply (review #2).
        self.assertGreaterEqual(cap["max_output_tokens"], 4096)

    def test_errored_tool_call_is_not_a_receipt(self):
        # A failed propose_change must NOT count toward the receipt, else it would
        # back a phantom "I opened a PR" claim (review #3).
        output = [
            SimpleNamespace(type="mcp_call", name="read_findings", error=None),
            SimpleNamespace(type="mcp_call", name="propose_change",
                            error={"message": "auth failed"}),
            SimpleNamespace(type="message", content=[SimpleNamespace(text="done")]),
        ]
        self.enterContext(mock.patch.dict(
            sys.modules, {"openai": _fake_openai_module(output=output, output_text="done")}))
        # argo_incidents.record_incident hits a real ledger; stub it.
        import argo_incidents
        self.enterContext(mock.patch.object(
            argo_incidents, "record_incident", lambda *a, **k: None))
        servers = [{"name": "argo", "url": "https://x/mcp/mcp",
                    "authorization_token": "tok"}]
        _, events = observe.chat_with_mcp(
            "SYS", [{"role": "user", "content": "open a PR"}], "gpt-5.5",
            mcp_servers=servers, return_tool_events=True)
        self.assertEqual(events, ["read_findings"])  # only the succeeded call

    def test_no_server_means_no_tools_sent(self):
        cap = {}
        self.enterContext(mock.patch.dict(sys.modules, {"openai": _fake_openai_module(cap)}))
        observe.chat_with_mcp("SYS", [{"role": "user", "content": "hi"}], "gpt-4.1",
                              mcp_servers=None, return_tool_events=True)
        self.assertNotIn("tools", cap)  # no server -> a plain, tool-less Responses call


class OpenAiToolFallbackTest(unittest.TestCase):
    """_generate_reply: Claude errors, GPT answers via the Responses tool path with a
    server configured -> a receipt-backed claim passes and NO fallback notice fires."""

    def setUp(self):
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: None
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))
        self.enterContext(mock.patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}))
        # A server IS configured this turn, so GPT keeps tools.
        self.enterContext(mock.patch.object(
            wh, "MCP_SERVERS",
            [{"name": "argo", "url": "https://x/mcp/mcp", "authorization_token": "t"}]))
        self.enterContext(mock.patch.object(wh, "_route_model", lambda t: "claude-x"))
        self.enterContext(mock.patch.object(observe, "resolve_models", lambda: ["gpt-x"]))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY"}
            if m == "claude-x" else {"name": "openai", "key_env": "OPENAI_API_KEY"}))
        self.enterContext(mock.patch.object(wh, "build_system_prompt", lambda: "SYS"))
        self.enterContext(mock.patch.object(wh, "_recent_turns", lambda c: []))
        self.enterContext(mock.patch.object(wh, "_clean_reply", lambda s: s))
        self.enterContext(mock.patch.object(wh.profile, "name", lambda: "User"))
        self.enterContext(mock.patch.object(
            wh.argo_memory, "record_many", lambda *a, **k: None))

    def test_gpt_keeps_tools_when_claude_errors(self):
        chat = mock.Mock(side_effect=[
            Exception("credit balance is too low"),          # claude down
            ("Opened the PR for review: link", ["propose_change"]),  # gpt + tools
        ])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        # generate_observations is the tool-LESS path; it must NOT be used here.
        self.enterContext(mock.patch.object(
            observe, "generate_observations",
            mock.Mock(side_effect=AssertionError("tool-less path must not run"))))
        out = wh._generate_reply(7, "open a PR for the fix", "open a PR for the fix")
        self.assertEqual(out, "Opened the PR for review: link")
        self.assertFalse(out.startswith(wh._FALLBACK_NOTICE))   # it HAS tools
        self.assertFalse(self.noted)  # receipt-backed claim, no incident

    def test_empty_gpt_reply_is_not_sent(self):
        # An empty reply (e.g. reasoning ate the whole token budget) must not be sent
        # as a blank message -- it falls through to the honest error (review #2).
        chat = mock.Mock(side_effect=[
            Exception("credit balance is too low"),  # claude down
            ("", []),                                # gpt returns nothing usable
        ])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        out = wh._generate_reply(7, "what's new", "what's new")
        self.assertTrue(out.startswith("(Argo hit an error"))
        self.assertNotEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
