"""SHIPPED / DROPPED gate tests (argo_webhook.handle_update).

The human grades a committed bet's outcome, closing the judgment loop. Like
FIX/SELECT, this gate is deterministic and sits UPSTREAM of the model: it must
(1) route on a strict word match so casual prose ('shipped it last night') falls
through to the model instead of hijacking a sentence, (2) target the SELECTED bet,
and (3) only claim it graded a call when a prediction is actually bound and still
pending. Peer gates (test_webhook_fix_gate, test_webhook_evolve_gate) test the
webhook wiring directly; this does the same for the outcome gate.

Pure + hermetic: tmp stores, send_telegram + the model path stubbed, no network.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_observe as observe
import argo_predictions as pred
import argo_store
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class OutcomeGateTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.projects = base / "projects.json"
        self.enterContext(mock.patch.object(wh, "PROJECTS_LOG", self.projects))
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # A handled SHIPPED/DROPPED must NEVER reach the model.
        self.model = mock.Mock(side_effect=AssertionError("model path must not run"))
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", self.model))

    def _seed(self, **extra):
        entry = {"id": "P-001", "text": "build it",
                 "selected": True, "selected_at": "2026-06-18 10:00 UTC"}
        entry.update(extra)
        argo_store.save_json(self.projects, [entry])

    def _bind_pred(self):
        pid = pred.record("WM-001", "claim",
                          {"kind": "project_shipped", "project_id": "P-001"}, 14)
        log = argo_store.load_json(self.projects, [])
        log[0]["judgment_prediction_id"] = pid
        argo_store.save_json(self.projects, log)

    def test_shipped_routes_deterministically_and_marks_and_claims(self):
        self._seed()
        self._bind_pred()
        wh.handle_update(_update("SHIPPED"))
        self.assertTrue(argo_store.load_json(self.projects, [])[0]["shipped"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("P-001", self.sent[0])
        self.assertIn("grade", self.sent[0].lower())

    def test_shipped_is_case_insensitive_and_takes_project_id(self):
        self._seed()
        self._bind_pred()
        wh.handle_update(_update("shipped p-001"))
        self.assertTrue(argo_store.load_json(self.projects, [])[0]["shipped"])

    def test_dropped_routes_and_marks(self):
        self._seed()
        self._bind_pred()
        wh.handle_update(_update("DROPPED"))
        rows = argo_store.load_json(self.projects, [])
        self.assertTrue(rows[0]["dropped"])
        self.assertNotIn("shipped", rows[0])

    def test_casual_shipped_prose_does_not_hijack_the_bet(self):
        # Not a command: must not mark the selected bet shipped. (The reply path
        # itself isn't under test, so tolerate it erroring in the stubbed harness.)
        self._seed()
        self.model.side_effect = None
        self.model.return_value = "noted"
        try:
            wh.handle_update(_update("shipped it last night, felt great"))
        except Exception:
            pass
        self.assertNotIn("shipped", argo_store.load_json(self.projects, [])[0])

    def test_killed_then_shipped_message_is_honest(self):
        # post-void state: rehearsed (to KILL) but no live prediction bound. The
        # 'none' message must not falsely claim the bet was never rehearsed.
        argo_store.save_json(self.projects, [{
            "id": "P-001", "text": "x", "selected": True,
            "selected_at": "2026-06-18 10:00 UTC", "verdict": "KILL",
            "rehearsed_at": "2026-06-18 09:00"}])
        wh.handle_update(_update("SHIPPED"))
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("hadn't rehearsed", self.sent[0])
        self.assertTrue(argo_store.load_json(self.projects, [])[0]["shipped"])

    def test_bare_shipped_with_nothing_selected(self):
        argo_store.save_json(self.projects, [{"id": "P-001", "text": "x",
                                              "shown_at": "2026-06-18 09:00 UTC"}])
        wh.handle_update(_update("SHIPPED"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing selected", self.sent[0])


if __name__ == "__main__":
    unittest.main()
