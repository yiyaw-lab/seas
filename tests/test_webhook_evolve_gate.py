"""EVOLVE / SKIP gate tests (argo_webhook.handle_update).

The frontier loop offers a stack upgrade; the user replies EVOLVE or SKIP. Like
FIX/IGNORE this gate is deterministic and sits UPSTREAM of the model: EVOLVE acks then
runs the staged lever (rehearse + a real PR, never a narrated phantom), SKIP declines
and mutes it. Neither may reach the LLM.

Pure + hermetic: argo_evolve is faked via sys.modules so there's no feed fetch, no
rehearsal, no GitHub.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import sys
import types
import unittest
from unittest import mock

import argo_observe as observe
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class EvolveGateTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))

        self.calls = []
        self.pending = True
        fake = types.ModuleType("argo_evolve")
        fake.has_pending = lambda: self.pending
        fake.accept_pending = lambda: (self.calls.append("accept") or
                                       "Drafted the upgrade and opened http://pr/9.")
        fake.decline_pending = lambda: (self.calls.append("decline") or
                                        "Dropped it. I won't bring up batch_api again for a month.")
        self.enterContext(mock.patch.dict(sys.modules, {"argo_evolve": fake}))

        # Neither branch may reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))

    def test_evolve_acks_then_sends_real_result(self):
        wh.handle_update(_update("EVOLVE"))
        self.assertEqual(self.calls, ["accept"])
        self.assertEqual(len(self.sent), 2)        # ack + result
        self.assertIn("on it", self.sent[0])
        self.assertIn("http://pr/9", self.sent[1])

    def test_evolve_with_nothing_staged_is_one_honest_line(self):
        self.pending = False
        wh.handle_update(_update("EVOLVE"))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing staged", self.sent[0])

    def test_skip_declines_and_does_not_accept(self):
        wh.handle_update(_update("SKIP"))
        self.assertEqual(self.calls, ["decline"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Dropped", self.sent[0])

    def test_evolve_is_case_insensitive(self):
        wh.handle_update(_update("evolve"))
        self.assertEqual(self.calls, ["accept"])


if __name__ == "__main__":
    unittest.main()
