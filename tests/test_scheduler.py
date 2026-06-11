"""Scheduler regression tests — the cron-drift / missing-delivery area.

Locks the grace-window + per-day dedupe behavior in argo_scheduled. These are the
bugs that silently dropped a weekly delivery: GitHub's cron drifts past the exact
hour, so a window must still fire within a grace period, but exactly once.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import argo_scheduled as sched


def _now(hour, day=15, month=6, year=2026):
    # 2026-06-15 is a Monday (weekday() == 0); use day=19 for Friday (== 4).
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


class DueHourTest(unittest.TestCase):
    def test_exact_hour_fires(self):
        self.assertEqual(sched._due_hour({"hour": [15]}, _now(15)), 15)

    def test_grace_window_fires_delayed_window(self):
        # Window scheduled for 14:00, cron lands at 17:00 (3h late) -> still fires,
        # keyed to the SCHEDULED hour so dedupe stays stable.
        self.assertEqual(sched._due_hour({"hour": [14]}, _now(17)), 14)

    def test_past_grace_returns_none(self):
        # 4h late is beyond GRACE_HOURS=3.
        self.assertIsNone(sched._due_hour({"hour": [14]}, _now(18)))

    def test_future_hour_returns_none(self):
        self.assertIsNone(sched._due_hour({"hour": [15]}, _now(13)))

    def test_disabled_returns_none(self):
        self.assertIsNone(sched._due_hour({"enabled": False, "hour": [15]}, _now(15)))

    def test_day_mismatch_returns_none(self):
        fri = {"days": [4], "hour": [15]}
        self.assertIsNone(sched._due_hour(fri, _now(15, day=15)))      # Monday
        self.assertEqual(sched._due_hour(fri, _now(15, day=19)), 15)   # Friday

    def test_daily_always_eligible(self):
        daily = {"days": "daily", "hour": [15]}
        self.assertEqual(sched._due_hour(daily, _now(15, day=15)), 15)
        self.assertEqual(sched._due_hour(daily, _now(15, day=19)), 15)

    def test_picks_latest_candidate(self):
        # Back-to-back windows: at 17:00 both 14 and 16 are within grace; fire 16.
        self.assertEqual(sched._due_hour({"hour": [14, 16]}, _now(17)), 16)


class FireKeyTest(unittest.TestCase):
    def test_key_stable_across_grace(self):
        s = {"name": "weekly-project", "command": "project"}
        on_time = sched._fire_key(s, _now(14), target_hour=14)
        late = sched._fire_key(s, _now(17), target_hour=14)
        self.assertEqual(on_time, late)
        self.assertEqual(on_time, "weekly-project@2026-06-15T14")

    def test_key_falls_back_to_command_name(self):
        key = sched._fire_key({"command": "watch"}, _now(9), target_hour=9)
        self.assertTrue(key.startswith("watch@"))


class MainDedupeTest(unittest.TestCase):
    """End-to-end main() run with run_command stubbed, so no network / no LLM."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule = self.tmp / "schedule.json"
        self.state = self.tmp / "schedule_state.json"
        self.enterContext(mock.patch.object(sched, "SCHEDULE_PATH", self.schedule))
        self.enterContext(mock.patch.object(sched, "STATE_PATH", self.state))
        # A single daily window. Use a fixed "now" so the test is time-independent.
        self.schedule.write_text(json.dumps({"schedules": [
            {"name": "weekly-project", "days": "daily", "hour": [15],
             "command": "project", "enabled": True}
        ]}))
        self.fixed_now = _now(15)
        self.calls = []
        self.enterContext(mock.patch.object(
            sched, "run_command", lambda cmd: self.calls.append(cmd)))

    def _run(self):
        with mock.patch.object(sched, "datetime") as dt:
            dt.now.return_value = self.fixed_now
            sched.main()

    def test_fires_when_unfired_and_records_key(self):
        self._run()
        self.assertEqual(self.calls, ["project"])
        state = json.loads(self.state.read_text())
        self.assertIn("weekly-project@2026-06-15T15", state["fired"])

    def test_does_not_refire_already_fired_key(self):
        self.state.write_text(json.dumps(
            {"fired": ["weekly-project@2026-06-15T15"]}))
        self._run()
        self.assertEqual(self.calls, [])  # deduped: never re-sent


class LocalCommandsTest(unittest.TestCase):
    """The webhook's in-process scheduler half: fire_due(only=...) restricts to the
    volume-dependent commands, and its dedupe state is a SEPARATE file (sharing the
    Actions-committed one would let an inert Actions fire consume the webhook's key)."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule = self.tmp / "schedule.json"
        self.state = self.tmp / "schedule_state.json"
        self.local_state = self.tmp / "schedule_state_local.json"
        self.enterContext(mock.patch.object(sched, "SCHEDULE_PATH", self.schedule))
        self.enterContext(mock.patch.object(sched, "STATE_PATH", self.state))
        self.schedule.write_text(json.dumps({"schedules": [
            {"name": "weekly-project", "days": "daily", "hour": [15],
             "command": "project", "enabled": True},
            {"name": "frontier", "days": "daily", "hour": [15],
             "command": "frontier", "enabled": True},
        ]}))
        self.calls = []
        self.enterContext(mock.patch.object(
            sched, "run_command", lambda cmd: self.calls.append(cmd)))

    def _fire(self, **kwargs):
        with mock.patch.object(sched, "datetime") as dt:
            dt.now.return_value = _now(15)
            return sched.fire_due(**kwargs)

    def test_frontier_command_is_registered(self):
        self.assertEqual(sched.COMMANDS["frontier"], ("argo_evolve", "run_cli"))
        self.assertIn("frontier", sched.LOCAL_COMMANDS)
        self.assertIn("diagnose", sched.LOCAL_COMMANDS)

    def test_only_filter_fires_just_the_local_commands(self):
        ran = self._fire(only=("frontier",), state_path=self.local_state)
        self.assertEqual(ran, ["frontier"])
        self.assertEqual(self.calls, ["frontier"])

    def test_local_state_is_isolated_from_the_actions_state(self):
        self._fire(only=("frontier",), state_path=self.local_state)
        self.assertTrue(self.local_state.exists())
        self.assertFalse(self.state.exists())  # the shared file is untouched
        # A key fired by the Actions runner must NOT block the local pass.
        self.state.write_text(json.dumps({"fired": ["frontier@2026-06-15T15"]}))
        self.local_state.unlink()
        ran = self._fire(only=("frontier",), state_path=self.local_state)
        self.assertEqual(ran, ["frontier"])


if __name__ == "__main__":
    unittest.main()
