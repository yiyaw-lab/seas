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

import argo_paths
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
            {"name": "weekly-reflection", "days": "daily", "hour": [15],
             "command": "reflect", "enabled": True},
            {"name": "capability-gaps", "days": "daily", "hour": [15],
             "command": "gaps", "enabled": True},
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
        # reflect is volume-bound (its real project ratings live on the Railway
        # volume, not a fresh Actions checkout), so it must run in the local loop too.
        self.assertIn("reflect", sched.LOCAL_COMMANDS)

    def test_gaps_command_is_registered(self):
        # The proactive capability-gap proposer: same volume + EVOLVE gate as
        # frontier, so it must be reachable from the webhook's local loop.
        self.assertEqual(sched.COMMANDS["gaps"], ("argo_evolve", "run_gaps_cli"))
        self.assertIn("gaps", sched.LOCAL_COMMANDS)

    def test_gaps_fires_under_local_commands_filter(self):
        ran = self._fire(only=sched.LOCAL_COMMANDS, state_path=self.local_state)
        self.assertIn("gaps", ran)
        self.assertNotIn("project", ran)  # not volume-bound: stays on Actions

    def test_only_filter_fires_just_the_local_commands(self):
        ran = self._fire(only=("frontier",), state_path=self.local_state)
        self.assertEqual(ran, ["frontier"])
        self.assertEqual(self.calls, ["frontier"])

    def test_reflect_fires_under_local_commands_filter(self):
        # The fix: reflect must be reachable from the webhook's local loop
        # (only=LOCAL_COMMANDS), not only the Actions runner where its project-ratings
        # input is always empty. project (not volume-bound) stays out of the local pass.
        ran = self._fire(only=sched.LOCAL_COMMANDS, state_path=self.local_state)
        self.assertIn("reflect", ran)
        self.assertNotIn("project", ran)

    def test_local_state_is_isolated_from_the_actions_state(self):
        self._fire(only=("frontier",), state_path=self.local_state)
        self.assertTrue(self.local_state.exists())
        self.assertFalse(self.state.exists())  # the shared file is untouched
        # A key fired by the Actions runner must NOT block the local pass.
        self.state.write_text(json.dumps({"fired": ["frontier@2026-06-15T15"]}))
        self.local_state.unlink()
        ran = self._fire(only=("frontier",), state_path=self.local_state)
        self.assertEqual(ran, ["frontier"])


class WatchPlacementTest(unittest.TestCase):
    """The tripwire 'watch' sweep moved off GitHub's throttled hourly cron onto the
    webhook's reliable local_loop, with its seen-store on the Railway volume. So:
    (1) watch must be a LOCAL_COMMAND (fired by local_loop), and (2) the Actions
    runner's main() must NOT fire it -- otherwise watch double-sends, writing a
    second repo-committed seen-store that never dedupes against the volume one.
    Both assertions fail before the move (watch was Actions-only) and pass after."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule = self.tmp / "schedule.json"
        self.state = self.tmp / "schedule_state.json"
        self.local_state = self.tmp / "schedule_state_local.json"
        self.enterContext(mock.patch.object(sched, "SCHEDULE_PATH", self.schedule))
        self.enterContext(mock.patch.object(sched, "STATE_PATH", self.state))
        # Both windows due at 15:00: a watch sweep and a non-volume project delivery.
        self.schedule.write_text(json.dumps({"schedules": [
            {"name": "tripwire", "days": "daily", "hour": [15],
             "command": "watch", "enabled": True},
            {"name": "weekly-project", "days": "daily", "hour": [15],
             "command": "project", "enabled": True},
        ]}))
        self.calls = []
        self.enterContext(mock.patch.object(
            sched, "run_command", lambda cmd: self.calls.append(cmd)))

    def test_watch_is_volume_bound(self):
        self.assertEqual(sched.COMMANDS["watch"], ("argo_watch", "main"))
        self.assertIn("watch", sched.LOCAL_COMMANDS)

    def test_actions_main_does_not_fire_watch(self):
        # The Actions entrypoint fires only the non-volume deliveries (project),
        # never watch -- that's what prevents the double-send into two seen-stores.
        with mock.patch.object(sched, "datetime") as dt:
            dt.now.return_value = _now(15)
            sched.main()
        self.assertEqual(self.calls, ["project"])
        self.assertNotIn("watch", self.calls)

    def test_watch_fires_in_the_local_loop_pass(self):
        with mock.patch.object(sched, "datetime") as dt:
            dt.now.return_value = _now(15)
            ran = sched.fire_due(only=sched.LOCAL_COMMANDS, state_path=self.local_state)
        self.assertIn("watch", ran)
        self.assertNotIn("project", ran)  # non-volume: stays on the Actions runner


class ShippedScheduleTest(unittest.TestCase):
    """Guards the live data/schedule.json. Enabling the frontier loop (and its
    inward twin, gaps) is a data flip the webhook's local_loop reads at deploy time,
    so a silent regression to enabled:false would re-inert them -- exactly the
    'structurally inert' failure this repo keeps hitting. Reads the real file only;
    no network."""

    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(Path(argo_paths.SCHEDULE_PATH).read_text())
        cls.by_cmd = {s["command"]: s for s in cls.config["schedules"]}

    def test_frontier_loop_is_enabled(self):
        self.assertTrue(self.by_cmd["frontier"]["enabled"])

    def test_capability_gaps_entry_present_and_enabled(self):
        self.assertIn("gaps", self.by_cmd)
        self.assertTrue(self.by_cmd["gaps"]["enabled"])

    def test_every_scheduled_command_maps_to_a_handler(self):
        for cmd in self.by_cmd:
            self.assertIn(cmd, sched.COMMANDS)

    def test_capability_gaps_runs_before_frontier(self):
        # gaps shares frontier's one-nudge-a-day budget; scheduling it an earlier
        # hour lets a same-pass grace-window fire (sorted by hour) hand the weekly
        # inward proposal that day's slot instead of frontier.
        self.assertLess(self.by_cmd["gaps"]["hour"], self.by_cmd["frontier"]["hour"])


class FireOrderTest(unittest.TestCase):
    """When a delayed/restarted pass finds several windows due at once (the grace
    window), the earlier scheduled hour must fire first so it wins any shared
    resource -- the evolution loop's one-nudge-a-day budget that frontier (17:00)
    and gaps (16:00) share. Guards against schedule.json array order deciding it."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule = self.tmp / "schedule.json"
        self.state = self.tmp / "state.json"
        self.enterContext(mock.patch.object(sched, "SCHEDULE_PATH", self.schedule))
        self.enterContext(mock.patch.object(sched, "STATE_PATH", self.state))
        # Deliberately list the LATER hour first: only the hour-sort can fix order.
        self.schedule.write_text(json.dumps({"schedules": [
            {"name": "later", "days": "daily", "hour": [17], "command": "frontier",
             "enabled": True},
            {"name": "earlier", "days": "daily", "hour": [16], "command": "gaps",
             "enabled": True},
        ]}))
        self.calls = []
        self.enterContext(mock.patch.object(
            sched, "run_command", lambda cmd: self.calls.append(cmd)))

    def test_lower_hour_fires_first_when_both_due_in_one_pass(self):
        # now=17:xx: hour 16 (within grace) and hour 17 (exact) are both due in one
        # pass; gaps (16) must run before frontier (17) despite the array order.
        with mock.patch.object(sched, "datetime") as dt:
            dt.now.return_value = _now(17)
            sched.fire_due(state_path=self.state)
        self.assertEqual(self.calls, ["gaps", "frontier"])


if __name__ == "__main__":
    unittest.main()
