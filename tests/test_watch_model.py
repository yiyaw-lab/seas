"""Watch model-resolution test — the other half of the tripwire-outage fix.

A set-but-empty ARGO_CHAT_MODEL (how the CI var arrives when unset) must NOT defeat
the claude-sonnet default and route the judge to ARGO_MODEL (gpt-5). Pure: the model
call is stubbed, so no network or real key.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import unittest
from unittest import mock

import argo_watch as watch


class JudgeModelResolutionTest(unittest.TestCase):
    def test_empty_chat_model_falls_back_to_sonnet(self):
        captured = {}

        def fake_chat(system, messages, model, **kw):
            captured["model"] = model
            captured["temperature"] = kw.get("temperature")
            return "NONE"  # nothing clears the bar -> judge returns []

        env = {"ARGO_CHAT_MODEL": "", "ARGO_MODEL": "gpt-5",
               "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(watch.observe, "chat_with_mcp", fake_chat), \
                mock.patch.object(watch.observe, "generate_observations",
                                  lambda *a, **k: self.fail(
                                      "empty ARGO_CHAT_MODEL must not route to OpenAI")):
            result = watch.judge([{"title": "t", "summary": "s", "link": "l"}])

        self.assertEqual(result, [])
        self.assertTrue(captured["model"].startswith("claude-sonnet"),
                        f"expected a sonnet model, got {captured['model']!r}")
        self.assertEqual(captured["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
