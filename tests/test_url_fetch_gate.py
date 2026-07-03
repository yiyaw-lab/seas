"""URL-before-fetch gate (argo_bluff.url_fetch_gap via argo_webhook._generate_reply).

The gate is the code enforcement of Argo's own most-logged chat_weakness: it
composed replies ABOUT a URL before the fetch path ever ran, so the guardrail
existed only as a stated intention. A URL in the user's turn with no read-family
tool fired forces ONE re-attempt whose gap note demands fetch-or-disclaim.

Pure: chat_with_mcp is mocked; no network/LLM/real stores.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from unittest import mock

import argo_observe as observe
import argo_webhook as wh

_SERVERS = [{"name": "argo", "url": "https://x/mcp/mcp", "authorization_token": "t"}]


class UrlFetchGateTest(unittest.TestCase):
    """_generate_reply runs pure (patch set mirrors OpenAiToolFallbackTest)."""

    def setUp(self):
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: None
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))
        self.enterContext(mock.patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}))
        self.enterContext(mock.patch.object(wh, "MCP_SERVERS", _SERVERS))
        self.enterContext(mock.patch.object(wh, "_route_model", lambda t: "claude-x"))
        self.enterContext(mock.patch.object(observe, "resolve_models", lambda: []))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY",
                       "supports_mcp": True}))
        self.enterContext(mock.patch.object(
            wh, "build_system_prompt", lambda *a, **kw: "SYS"))
        self.enterContext(mock.patch.object(
            wh.argo_cmo, "is_active", lambda c: False))
        self.enterContext(mock.patch.object(wh, "_recent_turns", lambda c: []))
        self.enterContext(mock.patch.object(wh.profile, "name", lambda: "User"))
        self.enterContext(mock.patch.object(
            wh.argo_memory, "record_many", lambda *a, **k: None))

    def test_url_with_no_fetch_forces_one_reattempt(self):
        chat = mock.Mock(side_effect=[
            ("summary from priors", []),                     # no tool fired
            ("answering from the text you pasted only", []),  # forced redo
        ])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        msg = "what do you think of https://example.com/post"
        out = wh._generate_reply(7, msg, msg)
        self.assertEqual(out, "answering from the text you pasted only")
        self.assertEqual(chat.call_count, 2)
        gap = chat.call_args_list[1].args[1][-1]["content"]
        self.assertIn("no read tool ran", gap)
        self.assertTrue(any(a[0] == "chat_weakness" for a in self.noted))

    def test_fetch_receipt_passes_gate(self):
        chat = mock.Mock(side_effect=[("the page says X", ["web_fetch"])])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        msg = "read https://example.com/post"
        out = wh._generate_reply(7, msg, msg)
        self.assertEqual(out, "the page says X")
        self.assertEqual(chat.call_count, 1)
        self.assertFalse(self.noted)

    def test_no_url_means_no_gate(self):
        chat = mock.Mock(side_effect=[("plain answer", [])])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        out = wh._generate_reply(7, "how are you", "how are you")
        self.assertEqual(out, "plain answer")
        self.assertEqual(chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
