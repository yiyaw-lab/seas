"""PROACTIVE command tests (argo_webhook.handle_update, F6).

The user tunes how rarely Argo pushes unprompted. Bare "PROACTIVE" reports the
current/effective threshold + act-on-rate; "PROACTIVE <n>" sets the base. Like the
other gates this is deterministic and UPSTREAM of the model -- tuning is exact and
never reaches the LLM. Plain text only (no markdown, no em dashes).

Pure + hermetic: both stores on a tmp dir, send_message captured, the model path
asserted unreachable. Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_observe as observe
import argo_pushes
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class ProactiveCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", tmp / "p.json"))
        self.enterContext(mock.patch.object(argo_pushes, "PROACTIVE_PATH", tmp / "t.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # The command must never reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))

    def test_set_then_report_round_trips_through_the_store(self):
        wh.handle_update(_update("PROACTIVE 0.6"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("0.60", self.sent[0])
        self.assertEqual(argo_pushes.get_threshold(), 0.6)

        # A bare PROACTIVE now reports the value we just set.
        self.sent.clear()
        wh.handle_update(_update("PROACTIVE"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("0.60", self.sent[0])

    def test_garbage_value_is_rejected_with_guidance_not_stored(self):
        wh.handle_update(_update("PROACTIVE loud"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("between 0 and 1", self.sent[0])
        # Nothing was written -> still the default.
        self.assertEqual(argo_pushes.get_threshold(), argo_pushes.DEFAULT_THRESHOLD)

    def test_case_insensitive(self):
        wh.handle_update(_update("proactive 0.25"))
        self.assertEqual(argo_pushes.get_threshold(), 0.25)

    def test_output_is_plain_text_no_markdown_or_em_dash(self):
        wh.handle_update(_update("PROACTIVE 0.4"))
        out = self.sent[0]
        for bad in ("*", "_", "`", "#", "—"):  # em dash
            self.assertNotIn(bad, out)


if __name__ == "__main__":
    unittest.main()
