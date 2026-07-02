"""_resolve_model() env-precedence tests (argo_evolve) -- the evolve loop's model
resolution order. ARGO_EVOLVE_MODEL wins if set (self-upgrade authoring can be
pinned to a premium/frontier tier, e.g. claude-fable-5, independently of the
routine-tier ARGO_CHAT_MODEL that watch/diagnose/self ride), then falls back to
ARGO_CHAT_MODEL_PREMIUM (the existing webhook/rehearse premium-escalation env),
then the routine default (ARGO_CHAT_MODEL or claude-sonnet-4-6), unchanged from
before this change.

Pure: os.environ is patched with mock.patch.dict, provider_for() is a real prefix
match (no network), and the "is a key set" check uses a fake ANTHROPIC_API_KEY so
no real credential is needed.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import argo_evolve as ev


class ResolveModelPrecedenceTest(unittest.TestCase):
    def setUp(self):
        # A live-looking key for every provider _resolve_model might route to, so
        # the "no key -> skip candidate" branch never masks the precedence test.
        self._env_patch = mock.patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x"}, clear=True)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_evolve_model_wins_over_everything(self):
        with mock.patch.dict("os.environ", {
                "ARGO_EVOLVE_MODEL": "claude-fable-5",
                "ARGO_CHAT_MODEL_PREMIUM": "claude-opus-4-8",
                "ARGO_CHAT_MODEL": "claude-sonnet-4-6"}):
            self.assertEqual(ev._resolve_model(), "claude-fable-5")

    def test_chat_model_premium_wins_when_evolve_model_unset(self):
        with mock.patch.dict("os.environ", {
                "ARGO_CHAT_MODEL_PREMIUM": "claude-opus-4-8",
                "ARGO_CHAT_MODEL": "claude-sonnet-4-6"}):
            self.assertEqual(ev._resolve_model(), "claude-opus-4-8")

    def test_falls_back_to_chat_model_when_neither_premium_env_set(self):
        with mock.patch.dict("os.environ", {"ARGO_CHAT_MODEL": "claude-sonnet-4-6"}):
            self.assertEqual(ev._resolve_model(), "claude-sonnet-4-6")

    def test_falls_back_to_default_when_nothing_set(self):
        # No ARGO_* model envs at all -> the unchanged routine default.
        self.assertEqual(ev._resolve_model(), "claude-sonnet-4-6")


if __name__ == "__main__":
    unittest.main()
