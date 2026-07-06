import sys
import tempfile
import types
import unittest
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


if __name__ == "__main__":
    unittest.main()
