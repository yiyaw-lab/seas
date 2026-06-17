"""xAI/Grok provider tests — model routing + the _call_xai chat-completions path.

Locks: provider_for routes grok-* to the new 'xai' row; supports_mcp is False (xAI
chat is OpenAI-compatible but its tools are a SEPARATE Agent Tools API, so grok must
stay off the MCP tool loop); _call_xai targets api.x.ai and keeps a custom temperature.
Pure: the openai SDK is faked in sys.modules and the guard is bypassed -- no network/key.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import argo_observe as observe


class XaiRoutingTest(unittest.TestCase):
    def test_provider_for_routes_grok(self):
        p = observe.provider_for("grok-4.3")
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "xai")
        self.assertEqual(p["key_env"], "XAI_API_KEY")
        self.assertIs(p["call"], observe._call_xai)

    def test_grok_does_not_support_mcp(self):
        # Load-bearing: grok must NOT take the remote-MCP tool path.
        self.assertFalse(observe.supports_mcp("grok-4.3"))

    def test_grok_keeps_custom_temperature(self):
        # Regression guard: do NOT add grok to _TEMPERATURE_REJECTING_PREFIXES.
        self.assertFalse(observe._rejects_temperature("grok-4.3"))

    def test_breaker_parity(self):
        self.assertIn("xai", observe._BREAKERS)

    def test_argo_model_grok_resolves(self):
        with mock.patch.dict(os.environ, {"ARGO_MODEL": "grok-4.3"}):
            self.assertEqual(observe.resolve_models(), ["grok-4.3"])

    def test_chat_with_mcp_rejects_grok(self):
        # grok has no MCP tool path -> chat_with_mcp must raise a clear error, not
        # fall through to the Anthropic client and crash. Raises before any SDK call.
        with self.assertRaises(ValueError):
            observe.chat_with_mcp("sys", [{"role": "user", "content": "hi"}],
                                  "grok-4.3")


class CallXaiTest(unittest.TestCase):
    @staticmethod
    def _fake_openai(captured):
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        mod = types.ModuleType("openai")

        def OpenAI(api_key=None, base_url=None):
            captured["_base_url"] = base_url
            captured["_api_key"] = api_key
            return client
        mod.OpenAI = OpenAI
        return mod

    def test_call_xai_targets_xai_base_url_with_temperature(self):
        captured = {}
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: do_call()), \
                mock.patch.dict(os.environ, {"XAI_API_KEY": "  k\n"}), \
                mock.patch.dict(sys.modules, {"openai": self._fake_openai(captured)}):
            out = observe._call_xai("job", "grok-4.3", temperature=0.4)
        self.assertEqual(out, "ok")
        self.assertEqual(captured["_api_key"], "k")  # key is .strip()-ed
        self.assertEqual(captured["_base_url"], "https://api.x.ai/v1")
        self.assertEqual(captured["model"], "grok-4.3")
        self.assertEqual(captured.get("temperature"), 0.4)
        self.assertEqual([m["role"] for m in captured["messages"]],
                         ["system", "user"])


if __name__ == "__main__":
    unittest.main()
