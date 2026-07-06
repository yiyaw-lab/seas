import sys
import tempfile
import types
import unittest
import json
from pathlib import Path
from unittest import mock

import argo_watch_runs


class WatchRunLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "watch_runs.json"
        patcher = mock.patch.object(argo_watch_runs, "WATCH_RUNS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_append_caps_and_returns_latest_first(self):
        with mock.patch.object(argo_watch_runs, "RUN_LEDGER_CAP", 2):
            for i in range(3):
                row = argo_watch_runs.new_run()
                row["started_at"] = f"2026-07-06T00:0{i}:00Z"
                row["candidates"] = i
                argo_watch_runs.append(row)

        rows = argo_watch_runs.recent(5)
        self.assertEqual([r["candidates"] for r in rows], [2, 1])

    def test_format_status_includes_delivery_receipt(self):
        row = argo_watch_runs.new_run()
        row.update({
            "started_at": "2026-07-06T00:00:00Z",
            "candidates": 12,
            "judge_kept": 3,
            "sent": 1,
            "suppressed": 2,
            "seen_store_written": True,
        })
        argo_watch_runs.add_suppression(row, "proactiveness gate")
        argo_watch_runs.add_error(row, "telegram send failed")
        argo_watch_runs.append(row)

        text = argo_watch_runs.format_status()

        self.assertIn("candidates=12", text)
        self.assertIn("kept=3", text)
        self.assertIn("sent=1", text)
        self.assertIn("suppressed=2", text)
        self.assertIn("seen_store=yes", text)
        self.assertIn("errors=1", text)
        self.assertIn("proactiveness gate", text)

    def test_watch_main_records_suppressed_delivery_receipt(self):
        sys.modules.setdefault(
            "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)
        )
        import argo_watch

        item = {"title": "Big model drop", "link": "https://example.com/drop"}
        result = types.SimpleNamespace(suppressed=True)
        seen_path = Path(self.tmp.name) / "seen.json"

        with mock.patch.object(argo_watch, "SEEN_PATH", seen_path), \
             mock.patch.object(argo_watch, "collect_new", return_value=[item]), \
             mock.patch.object(argo_watch, "collect_grok", return_value=[]), \
             mock.patch.object(
                 argo_watch, "judge", return_value=["Big model drop https://example.com/drop"]
             ), \
             mock.patch.object(argo_watch.argo_pushes, "post_to_webhook",
                               return_value=result), \
             mock.patch.object(argo_watch.send_telegram, "send_message") as send, \
             mock.patch.object(argo_watch.argo_memory, "record"), \
             mock.patch.object(sys, "argv", ["argo_watch.py"]):
            argo_watch.main()

        send.assert_not_called()
        row = argo_watch_runs.recent(1)[0]
        self.assertEqual(row["candidates"], 1)
        self.assertEqual(row["judge_kept"], 1)
        self.assertEqual(row["suppressed"], 1)
        self.assertEqual(row["sent"], 0)
        self.assertTrue(row["seen_store_written"])
        self.assertEqual(row["suppression_reasons"], ["proactiveness gate"])

    def test_watch_main_keeps_fresh_candidates_when_judge_returns_none(self):
        sys.modules.setdefault(
            "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)
        )
        import argo_watch

        items = [
            {"title": "Fresh model one", "summary": "A new model shipped.",
             "link": "https://example.com/one"},
            {"title": "Fresh tool two", "summary": "A new builder tool shipped.",
             "link": "https://example.com/two"},
            {"title": "Fresh paper three", "summary": "A fresh item.",
             "link": "https://example.com/three"},
        ]
        seen_path = Path(self.tmp.name) / "seen.json"
        sent = []
        result = types.SimpleNamespace(suppressed=False)

        with mock.patch.object(argo_watch, "SEEN_PATH", seen_path), \
             mock.patch.object(argo_watch, "collect_new", return_value=items), \
             mock.patch.object(argo_watch, "collect_grok", return_value=[]), \
             mock.patch.object(argo_watch, "judge", return_value=[]), \
             mock.patch.object(argo_watch.argo_pushes, "post_to_webhook",
                               return_value=result), \
             mock.patch.object(argo_watch.send_telegram, "send_message",
                               side_effect=lambda msg: sent.append(msg)), \
             mock.patch.object(argo_watch.argo_memory, "record"), \
             mock.patch.object(sys, "argv", ["argo_watch.py"]):
            argo_watch.main()

        self.assertEqual(len(sent), 2)
        self.assertIn("Fresh model one", sent[0])
        self.assertIn("Fresh tool two", sent[1])
        row = argo_watch_runs.recent(1)[0]
        self.assertEqual(row["candidates"], 3)
        self.assertEqual(row["judge_kept"], 2)
        self.assertEqual(row["sent"], 2)
        seen = json.loads(seen_path.read_text())
        self.assertEqual(seen["example.com/one"], argo_watch.MAX_ATTEMPTS)
        self.assertEqual(seen["example.com/two"], argo_watch.MAX_ATTEMPTS)
        self.assertEqual(seen["example.com/three"], 1)

    def test_watch_floor_does_not_keep_items_already_in_seen_store(self):
        sys.modules.setdefault(
            "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)
        )
        import argo_watch

        seen_path = Path(self.tmp.name) / "seen.json"
        seen_path.write_text('{"example.com/old": 1}')
        old = {"title": "Old retry", "summary": "Already seen.",
               "link": "https://example.com/old"}
        fresh = {"title": "Fresh candidate", "summary": "Never seen before.",
                 "link": "https://example.com/fresh"}
        sent = []
        result = types.SimpleNamespace(suppressed=False)

        with mock.patch.object(argo_watch, "SEEN_PATH", seen_path), \
             mock.patch.object(argo_watch, "collect_new", return_value=[old, fresh]), \
             mock.patch.object(argo_watch, "collect_grok", return_value=[]), \
             mock.patch.object(
                 argo_watch, "judge", return_value=["Old retry https://example.com/old"]
             ), \
             mock.patch.object(argo_watch.argo_pushes, "post_to_webhook",
                               return_value=result), \
             mock.patch.object(argo_watch.send_telegram, "send_message",
                               side_effect=lambda msg: sent.append(msg)), \
             mock.patch.object(argo_watch.argo_memory, "record"), \
             mock.patch.object(sys, "argv", ["argo_watch.py"]):
            argo_watch.main()

        self.assertEqual(len(sent), 1)
        self.assertIn("Fresh candidate", sent[0])
        self.assertNotIn("Old retry", sent[0])
        row = argo_watch_runs.recent(1)[0]
        self.assertEqual(row["candidates"], 2)
        self.assertEqual(row["judge_kept"], 1)
        self.assertEqual(row["sent"], 1)
        seen = json.loads(seen_path.read_text())
        self.assertEqual(seen["example.com/old"], 2)
        self.assertEqual(seen["example.com/fresh"], argo_watch.MAX_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
