"""Proactive pulse: resurface a forgotten high-energy project -- once, gently,
weekly-capped, deduped. The one owner-facing nudge not already covered by the
prediction grader / fix-confirm loop / critical-alert path.

Pure: PROJECTS_LOG + the incidents meta point at tmp; the send seam is stubbed.
This is the scheduler/incidents area, so per CLAUDE.md the feature carries tests.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argo_incidents as inc  # noqa: E402
import argo_pulse as pulse  # noqa: E402
import argo_store  # noqa: E402
import send_telegram  # noqa: E402


def _rated(n_days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=n_days_ago)).strftime("%Y-%m-%d %H:%M UTC")


def _shown(n_days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=n_days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class PulseTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.proj = base / "projects.json"
        self.enterContext(mock.patch.object(pulse, "PROJECTS_LOG", self.proj))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            send_telegram, "try_send_message", lambda t: self.sent.append(t) or True))

    def _write(self, projects):
        argo_store.save_json(self.proj, projects)

    def _meta(self):
        return inc.get_meta(pulse._PULSE_META_KEY, {}) or {}

    def test_nudges_stale_high_energy_project(self):
        self._write([{"id": "P-1", "energy": 9, "rated_at": _rated(15)}])
        pulse.run_cli()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("9/10", self.sent[0])
        self.assertIn("proj:P-1", self._meta().get("seen", []))
        self.assertTrue(self._meta().get("last_pulse_at"))

    def test_skips_low_energy(self):
        self._write([{"id": "P-1", "energy": 5, "rated_at": _rated(30)}])
        pulse.run_cli()
        self.assertEqual(self.sent, [])

    def test_skips_recently_rated(self):
        self._write([{"id": "P-1", "energy": 9, "rated_at": _rated(2)}])
        pulse.run_cli()
        self.assertEqual(self.sent, [])

    def test_shown_recently_keeps_project_fresh(self):
        # rated long ago but re-shown yesterday -> not stale (last touch is recent)
        self._write([{"id": "P-1", "energy": 9, "rated_at": _rated(40), "shown_at": _shown(1)}])
        pulse.run_cli()
        self.assertEqual(self.sent, [])

    def test_weekly_cooldown_blocks_second_pulse(self):
        self._write([
            {"id": "P-1", "energy": 9, "rated_at": _rated(15)},
            {"id": "P-2", "energy": 8, "rated_at": _rated(20)},
        ])
        pulse.run_cli()  # nudges the stalest
        pulse.run_cli()  # cooldown active -> no second send this week
        self.assertEqual(len(self.sent), 1)

    def test_picks_stalest_first(self):
        self._write([
            {"id": "P-1", "energy": 9, "rated_at": _rated(12)},
            {"id": "P-2", "energy": 9, "rated_at": _rated(25)},
        ])
        pulse.run_cli()
        seen = self._meta().get("seen", [])
        self.assertIn("proj:P-2", seen)      # the 25-day-old one
        self.assertNotIn("proj:P-1", seen)   # the fresher one waits its turn

    def test_dedup_no_second_nudge_for_same_project(self):
        self._write([{"id": "P-1", "energy": 9, "rated_at": _rated(15)}])
        pulse.run_cli()                       # nudges P-1
        meta = self._meta()
        meta["last_pulse_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(pulse._TS_FMT)
        inc.set_meta(pulse._PULSE_META_KEY, meta)  # open the weekly window again
        pulse.run_cli()                       # P-1 already seen -> no re-nudge
        self.assertEqual(len(self.sent), 1)

    def test_empty_log_is_noop(self):
        self._write([])
        pulse.run_cli()
        self.assertEqual(self.sent, [])

    def test_send_failure_marks_seen_and_does_not_retry(self):
        self._write([{"id": "P-1", "energy": 9, "rated_at": _rated(15)}])
        with mock.patch.object(send_telegram, "try_send_message", lambda t: False):
            pulse.run_cli()                   # delivery fails
        self.assertIn("proj:P-1", self._meta().get("seen", []))  # marked seen anyway
        self.assertFalse(self._meta().get("last_pulse_at"))      # cooldown NOT started
        pulse.run_cli()                       # P-1 seen -> no retry
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
