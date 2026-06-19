"""_author_fix_files: the EVOLVE/FIX drafting harness must actually land a PR for a
tractable change instead of declining on the first stumble.

Two failures made it return None (-> "I couldn't draft a fix I trust"): the draft
truncated mid-JSON because chat_with_mcp's 1024-token default is far too small for full
file bodies + a repro test, and a single-shot harness gave up on a recoverable near-miss.
These lock in the generous max_tokens, the one repair pass, and the no-retry-on-infra rule.

Pure: the model call is stubbed; no network/LLM. ANTHROPIC_API_KEY is faked so the
premium (Opus) model resolves.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import os
import unittest
from unittest import mock

import argo_mcp_server as srv
import argo_observe as observe


class AuthorFixRetryTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY"}))
        self.payload = {"description": "d", "suggestion": "s", "suspected_files": []}

    _GOOD = json.dumps({"files": {
        "src/x.py": "print('x')\n",
        "tests/test_x.py": "def test_x():\n    assert True\n"}})

    def test_repair_pass_recovers_a_near_miss(self):
        replies = iter(["here is the fix, but no json at all", self._GOOD])
        captured = []

        def fake_chat(system, messages, model, **kw):
            captured.append((list(messages), kw.get("max_tokens")))
            return next(replies)
        with mock.patch.object(observe, "chat_with_mcp", fake_chat):
            files = srv._author_fix_files(self.payload)

        self.assertEqual(files["src/x.py"], "print('x')\n")
        self.assertEqual(len(captured), 2)                       # one repair pass
        self.assertEqual(captured[0][1], srv._AUTHOR_MAX_TOKENS)  # generous, not 1024
        # the repair turn carried the rejected draft + a correction instruction
        repair_messages = captured[1][0]
        self.assertEqual(len(repair_messages), 3)               # user, assistant, user
        self.assertEqual(repair_messages[1]["role"], "assistant")
        self.assertIn("rejected", repair_messages[-1]["content"].lower())

    def test_empty_first_reply_does_not_send_empty_assistant_turn(self):
        # An empty draft (refusal / max_tokens before text) must NOT become an empty
        # assistant content block on the retry -- the API 400s on that, wasting the pass.
        replies = iter(["", self._GOOD])
        captured = []

        def fake_chat(system, messages, model, **kw):
            captured.append(list(messages))
            return next(replies)
        with mock.patch.object(observe, "chat_with_mcp", fake_chat):
            files = srv._author_fix_files(self.payload)

        self.assertEqual(files["src/x.py"], "print('x')\n")
        self.assertEqual(len(captured), 2)
        # the retry re-sent the original single user turn -- no empty assistant block
        self.assertEqual(len(captured[1]), 1)
        self.assertEqual(captured[1][0]["role"], "user")

    def test_infra_failure_does_not_retry(self):
        chat = mock.Mock(side_effect=RuntimeError("breaker open / no credits"))
        with mock.patch.object(observe, "chat_with_mcp", chat):
            self.assertIsNone(srv._author_fix_files(self.payload))
        self.assertEqual(chat.call_count, 1)                    # a retry can't fix infra

    def test_two_content_misses_gives_up(self):
        chat = mock.Mock(return_value="no json anywhere in here")
        with mock.patch.object(observe, "chat_with_mcp", chat):
            self.assertIsNone(srv._author_fix_files(self.payload))
        self.assertEqual(chat.call_count, 2)                    # tried the repair, then stopped


if __name__ == "__main__":
    unittest.main()
