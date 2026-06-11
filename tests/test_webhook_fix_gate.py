"""FIX / IGNORE gate tests (argo_webhook.handle_update).

The diagnostic loop offers a drafted self-fix; the user replies FIX or IGNORE. Like
CONFIRM/CANCEL, this gate is deterministic and sits UPSTREAM of the model: FIX runs the
staged propose_fix (a real PR, never a narrated phantom), IGNORE drops + mutes it. Neither
may reach the LLM.

Pure + hermetic: argo_mcp_server is faked via sys.modules so there's no FastMCP/network.

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


class FixGateTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))

        self.calls = []
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.run_pending_heal = lambda: (self.calls.append("run") or
                                              "Drafted a fix and opened http://pr/1 for review.")
        fake_mcp.decline_pending_fix = lambda: self.calls.append("decline")
        fake_mcp.clear_pending_heal = lambda: self.calls.append("clear")
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))

        # Neither branch may reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))

    def test_fix_runs_pending_heal_and_sends_real_url(self):
        wh.handle_update(_update("FIX"))
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("http://pr/1", self.sent[0])

    def test_ignore_declines_and_does_not_propose(self):
        wh.handle_update(_update("IGNORE"))
        self.assertEqual(self.calls, ["decline"])
        self.assertNotIn("run", self.calls)
        self.assertEqual(len(self.sent), 1)

    def test_fix_is_case_insensitive(self):
        wh.handle_update(_update("fix"))
        self.assertEqual(self.calls, ["run"])


if __name__ == "__main__":
    unittest.main()
