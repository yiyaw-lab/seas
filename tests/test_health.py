"""Health-endpoint tests (argo_webhook._health_payload).

The '/' route must return a JSON status from LOCAL files only -- no network --
and never raise, on both a fresh deploy (stores absent) and a running one (stores
present). Pure: path constants are patched to a temp dir, mirroring the
tests/test_scheduler.py idiom.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import argo_paths
import argo_self
import argo_webhook


class HealthPayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # Paths the payload + gather_performance read at call time.
        self.enterContext(mock.patch.object(
            argo_paths, "STATE_PATH", self.tmp / "schedule_state.json"))
        self.enterContext(mock.patch.object(
            argo_paths, "SIGNALS_PATH", self.tmp / "signals.json"))
        self.enterContext(mock.patch.object(
            argo_self, "PROJECTS_LOG", self.tmp / "argo_projects.json"))
        self.enterContext(mock.patch.object(
            argo_self, "SEEN_PATH", self.tmp / "argo_seen.json"))

    def test_fresh_deploy_all_stores_absent(self):
        """No state, no signals, no projects -- valid JSON, no crash, safe defaults."""
        p = argo_webhook._health_payload()
        self.assertEqual(p["status"], "ok")
        self.assertTrue(p["time"].endswith("Z"))
        self.assertEqual(p["recent_fires"], [])
        self.assertIsNone(p["signals_age_seconds"])
        # gather_performance tolerates missing files -> a zeroed snapshot, not None.
        self.assertIsNotNone(p["performance"])
        self.assertEqual(p["performance"]["projects_total"], 0)
        self.assertEqual(p["performance"]["projects_rated"], 0)
        # The whole payload must be JSON-serializable (it's returned via jsonify).
        json.dumps(p)

    def test_running_deploy_stores_present(self):
        """With real local state, the payload reflects fires, signal age, ratings."""
        fires = [f"watch@2026-06-08T{h:02d}" for h in range(8)]  # 8 -> keep last 5
        (self.tmp / "schedule_state.json").write_text(json.dumps({"fired": fires}))
        (self.tmp / "signals.json").write_text(json.dumps([{"title": "x"}]))
        (self.tmp / "argo_projects.json").write_text(json.dumps([
            {"id": "P-001", "energy": 9, "date": "2026-06-02"},
            {"id": "P-002", "energy": 7, "date": "2026-06-02"},
            {"id": "P-003", "date": "2026-06-05"},  # unrated
        ]))
        (self.tmp / "argo_seen.json").write_text(json.dumps(
            {"u1": 3, "u2": 3, "u3": 1}))  # 2 settled (>=3), 1 in-flight

        p = argo_webhook._health_payload()
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["recent_fires"], fires[-5:])      # last 5 only
        self.assertEqual(len(p["recent_fires"]), 5)
        self.assertIsInstance(p["signals_age_seconds"], int)
        self.assertGreaterEqual(p["signals_age_seconds"], 0)
        self.assertEqual(p["performance"]["projects_total"], 3)
        self.assertEqual(p["performance"]["projects_rated"], 2)
        self.assertEqual(p["performance"]["mean_energy"], 8.0)
        self.assertEqual(p["performance"]["tripwire_seen"], 3)
        self.assertEqual(p["performance"]["tripwire_settled"], 2)
        json.dumps(p)

    def test_corrupt_state_file_does_not_crash(self):
        """A truncated/garbage state file degrades to safe defaults, never raises."""
        (self.tmp / "schedule_state.json").write_text("{not valid json")
        p = argo_webhook._health_payload()
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["recent_fires"], [])


if __name__ == "__main__":
    unittest.main()
