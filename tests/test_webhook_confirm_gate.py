"""CONFIRM / CANCEL gate tests (argo_webhook.handle_update).

CONFIRM with a staged heal runs it deterministically, upstream of the model.
CONFIRM with NOTHING staged (the model offered CONFIRM in free text without
calling a heal tool) must not dead-end: the turn routes to the model so it can
stage the action for real, and a freshly staged SAFE heal (reregister_webhook /
refetch_signals) runs immediately on the okay the user already gave. A staged
propose_fix never auto-runs through this path.

Pure + hermetic: argo_mcp_server is faked via sys.modules for the gate tests;
the pending_heal_action() accessor is tested against the real module with
PENDING_HEAL_PATH pointed at a tmp dir.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import argo_observe as observe
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class ConfirmGateTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))

        self.calls = []
        self.pending = None  # what pending_heal_action() reports
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: self.pending
        fake_mcp.run_pending_heal = lambda: (self.calls.append("run") or
                                             "Re-registered the webhook.")
        fake_mcp.clear_pending_heal = lambda: self.calls.append("clear")
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))

        # The staged path and CANCEL must never reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))
        # Incident notes hit the real ledger; keep tests pure.
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))

    def test_confirm_with_staged_action_runs_it(self):
        self.pending = "reregister_webhook"
        reply_mock = self.enterContext(mock.patch.object(wh, "_generate_reply"))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(self.sent, ["Re-registered the webhook."])
        reply_mock.assert_not_called()

    def test_confirm_nothing_staged_routes_to_model(self):
        reply_mock = self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(return_value="On it now.")))
        wh.handle_update(_update("confirm"))
        self.assertEqual(self.calls, [])  # no exec: model staged nothing
        reply_mock.assert_called_once()
        chat_id, content, log_user_text = reply_mock.call_args.args[:3]
        self.assertIn("[system note", content)
        self.assertEqual(log_user_text, "confirm")
        self.assertEqual(self.sent, ["On it now."])
        self.assertTrue(self.noted)  # confirm_dead_end recorded

    def test_recovery_stages_safe_heal_and_runs_once(self):
        def stage_during_turn(*a, **k):
            self.pending = "refetch_signals"
            return "Refetching the feeds for you."
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(side_effect=stage_during_turn)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(self.sent,
                         ["Refetching the feeds for you.",
                          "Re-registered the webhook."])

    def test_recovery_never_auto_runs_propose_fix(self):
        def stage_fix(*a, **k):
            self.pending = "propose_fix"
            return "I drafted a fix, reply FIX to open the PR."
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(side_effect=stage_fix)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sent, ["I drafted a fix, reply FIX to open the PR."])

    def test_recovery_without_model_sends_plain_fallback(self):
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(return_value=None)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing was staged", self.sent[0])

    def test_cancel_clears_pending(self):
        wh.handle_update(_update("CANCEL"))
        self.assertEqual(self.calls, ["clear"])
        self.assertEqual(self.sent, ["Okay, dropped it."])


class PendingHealActionTest(unittest.TestCase):
    """pending_heal_action() against the real module, pending file in a tmp dir."""

    def setUp(self):
        import argo_mcp_server as srv
        self.srv = srv
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(tmp) / "argo_pending_heal.json"
        self.enterContext(mock.patch.object(srv, "PENDING_HEAL_PATH", self.path))

    def test_none_when_absent(self):
        self.assertIsNone(self.srv.pending_heal_action())

    def test_returns_staged_name(self):
        self.srv._stage_pending("reregister_webhook")
        self.assertEqual(self.srv.pending_heal_action(), "reregister_webhook")

    def test_none_on_corrupt_file(self):
        self.path.write_text("{not json")
        self.assertIsNone(self.srv.pending_heal_action())


if __name__ == "__main__":
    unittest.main()
