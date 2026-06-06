"""Temperature-guard tests — the tripwire-outage area.

Locks the fix for the bug that 400'd the watch judge on every run: a model that
rejects a custom `temperature` (claude-opus-4-8 outright; gpt-5/o-series accept only
the default) must have the param OMITTED, not sent as 0. Pure: the SDKs are faked in
sys.modules and the budget/breaker guard is bypassed, so no network or real key.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import argo_observe as observe


class RejectsTemperatureTest(unittest.TestCase):
    def test_reasoning_models_reject(self):
        for m in ["gpt-5", "gpt-5-mini", "o1", "o3-mini", "o4",
                  "claude-opus-4-8", "claude-opus-4-8-20260101"]:
            self.assertTrue(observe._rejects_temperature(m), m)

    def test_standard_models_accept(self):
        # gpt-4o / gpt-4.1 still take temperature=0 -- the watch judge relies on it.
        for m in ["gpt-4o", "gpt-4.1", "gpt-4", "claude-sonnet-4-6",
                  "claude-haiku-4-5"]:
            self.assertFalse(observe._rejects_temperature(m), m)


class CallOmitsTemperatureTest(unittest.TestCase):
    """The guard must actually drop `temperature` from the API kwargs, not just be
    consulted -- so a rejecting model never receives the param."""

    @staticmethod
    def _fake_openai(captured):
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        mod = types.ModuleType("openai")
        mod.OpenAI = lambda api_key=None: client
        return mod

    @staticmethod
    def _fake_anthropic(captured):
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        mod = types.ModuleType("anthropic")
        mod.Anthropic = lambda api_key=None: client
        return mod

    def _run_openai(self, model, captured):
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: do_call()), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}), \
                mock.patch.dict(sys.modules, {"openai": self._fake_openai(captured)}):
            observe._call_openai("job", model, temperature=0)

    def _run_anthropic(self, model, captured):
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: do_call()), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch.dict(sys.modules,
                                {"anthropic": self._fake_anthropic(captured)}):
            observe._call_anthropic("job", model, temperature=0)

    def test_openai_reasoning_model_omits_temperature(self):
        captured = {}
        self._run_openai("gpt-5", captured)
        self.assertNotIn("temperature", captured)

    def test_openai_standard_model_keeps_temperature(self):
        captured = {}
        self._run_openai("gpt-4o", captured)
        self.assertEqual(captured.get("temperature"), 0)

    def test_anthropic_opus_omits_temperature(self):
        captured = {}
        self._run_anthropic("claude-opus-4-8", captured)
        self.assertNotIn("temperature", captured)

    def test_anthropic_sonnet_keeps_temperature(self):
        captured = {}
        self._run_anthropic("claude-sonnet-4-6", captured)
        self.assertEqual(captured.get("temperature"), 0)


if __name__ == "__main__":
    unittest.main()
