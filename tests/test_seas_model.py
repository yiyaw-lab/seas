"""SEAS model-selection test — ARGO_SEAS_MODEL decouples the research model from
Argo's ARGO_MODEL fallback. Pure: sources + the model call are stubbed (no network,
no LLM, no disk writes — _extract_json is short-circuited to None right after the
captured model call).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import unittest
from unittest import mock

import seas_finding as sf


class SeasModelSelectionTest(unittest.TestCase):
    def _selected_model(self, env):
        captured = {}
        sig = {"title": "T", "summary": "s", "link": "https://x.com/a"}
        sources = [{"url": "https://x.com/a", "text": "aaa"},
                   {"url": "https://y.com/b", "text": "bbb"}]  # >= MIN_SOURCES

        def fake_gen(job, model, *a, **k):
            captured["model"] = model
            return "{}"

        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sf, "_gather_sources",
                                  lambda signal, dry_run=False: sources), \
                mock.patch.object(sf.probes, "should_investigate",
                                  lambda ref: (True, "")), \
                mock.patch.object(sf.observe, "generate_observations", fake_gen), \
                mock.patch.object(sf, "_extract_json", lambda reply: None):
            sf.investigate(sig, dry_run=False)
        return captured.get("model")

    def test_prefers_argo_seas_model(self):  # fails before the decouple (was gpt-5)
        m = self._selected_model({
            "ARGO_SEAS_MODEL": "claude-opus-4-8", "ARGO_MODEL": "gpt-5",
            "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"})
        self.assertEqual(m, "claude-opus-4-8")

    def test_falls_back_to_argo_model_when_unset(self):  # regression: fallback intact
        m = self._selected_model({
            "ARGO_MODEL": "gpt-5",
            "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"})
        self.assertEqual(m, "gpt-5")

    def test_preferred_without_key_falls_through(self):  # edge: preferred unkeyed
        m = self._selected_model({
            "ARGO_SEAS_MODEL": "claude-opus-4-8", "ARGO_MODEL": "gpt-5",
            "OPENAI_API_KEY": "o"})  # no ANTHROPIC_API_KEY
        self.assertEqual(m, "gpt-5")


if __name__ == "__main__":
    unittest.main()
