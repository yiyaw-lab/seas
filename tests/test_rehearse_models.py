"""Multi-model Rehearse — adversary model assignment: diverse minds + graceful
degradation. Pure: exercises only the resolution/assignment logic, never the SDK.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import unittest
from unittest import mock

import argo_observe as observe
import argo_rehearse as r


def _provider_names(models):
    return {observe.provider_for(m)["name"] for m in models}


class AssignAdversaryModelsTest(unittest.TestCase):
    ROLES = ("critic", "user", "ops")

    def _assign(self, env):
        # clear=True + ARGO_MODEL pinned so resolve_models() is deterministic
        # (no DEFAULT_MODELS pollution) and matches production where ARGO_MODEL is set.
        with mock.patch.dict(os.environ, env, clear=True):
            return r._assign_adversary_models(self.ROLES)

    def test_three_providers_three_distinct_minds(self):
        assigned = self._assign({
            "ARGO_MODEL": "gpt-5", "ANTHROPIC_API_KEY": "a",
            "OPENAI_API_KEY": "o", "XAI_API_KEY": "x"})
        self.assertEqual(len(set(assigned.values())), 3)  # 3 distinct models...
        self.assertEqual(_provider_names(assigned.values()),
                         {"anthropic", "openai", "xai"})   # ...one per provider

    def test_two_providers_two_distinct_no_crash(self):
        assigned = self._assign({
            "ARGO_MODEL": "gpt-5", "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"})
        self.assertEqual(len(set(assigned.values())), 2)   # diversity preserved
        self.assertEqual(set(assigned), set(self.ROLES))   # every role assigned

    def test_one_provider_degrades_and_logs(self):
        env = {"ARGO_MODEL": "gpt-5", "ANTHROPIC_API_KEY": "a"}
        with mock.patch.dict(os.environ, env, clear=True), \
                self.assertLogs("argo_rehearse", level="INFO") as cm:
            assigned = r._assign_adversary_models(self.ROLES)
        self.assertEqual(len(set(assigned.values())), 1)   # all share one model
        self.assertTrue(any("degraded" in line for line in cm.output))

    def test_no_provider_returns_none(self):
        self.assertIsNone(self._assign({"ARGO_MODEL": "gpt-5"}))  # no keys at all

    def test_grok_routes_to_xai_provider(self):  # depends on S0
        self.assertEqual(observe.provider_for("grok-4.3")["name"], "xai")


if __name__ == "__main__":
    unittest.main()
