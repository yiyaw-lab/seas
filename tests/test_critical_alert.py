"""Proactive critical-failure ping: a SEVERE incident (circuit_open / budget_exceeded)
queues a heads-up that the local_loop tick delivers within one cycle, instead of the
owner only learning from the daily diagnose (or a log). record_incident NEVER sends
(send_telegram records incidents on failure -> re-entrancy); the tick owns delivery,
daily-capped so a flapping failure can't spam.

Regression guard for the gap Argo surfaced ("i can't initiate -- you have to come to
me" / "i only know failures from a log, not from experiencing them"). Pure: incidents
path -> tmp, send seam injected. This is the scheduler/incidents area, so per CLAUDE.md
a fix here must carry a failing-before/passing-after test.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argo_incidents as inc  # noqa: E402
import argo_scheduled as sched  # noqa: E402


class CriticalAlertQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH",
                                            Path(self.tmp) / "inc.json"))

    def _pending(self):
        alert = inc.get_meta(inc._CRITICAL_ALERT_KEY, {}) or {}
        return alert.get("pending")

    def test_severe_kind_queues_a_pending_alert(self):
        inc.record_incident("circuit_open", "anthropic")
        p = self._pending()
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "circuit_open")

    def test_non_severe_kind_does_not_queue(self):
        inc.record_incident("delivery_failure", "telegram 500")
        self.assertIsNone(self._pending())

    def test_drain_sends_once_then_clears(self):
        inc.record_incident("budget_exceeded", "daily call cap reached")
        sent = []
        out = inc.drain_critical_alert(lambda t: sent.append(t) or True)
        self.assertEqual(len(sent), 1)
        self.assertIn("budget", sent[0].lower())
        self.assertIsNotNone(out)
        self.assertIsNone(self._pending())  # cleared after delivery

    def test_drain_is_noop_when_nothing_pending(self):
        sent = []
        out = inc.drain_critical_alert(lambda t: sent.append(t) or True)
        self.assertEqual(sent, [])
        self.assertIsNone(out)

    def test_drain_does_not_clobber_a_write_made_during_send(self):
        # send_telegram records a delivery_failure incident when delivery fails. The
        # drain must not overwrite that nested write with a stale pre-send snapshot.
        inc.record_incident("circuit_open", "anthropic")

        def failing_send(text):
            inc.record_incident("delivery_failure", "telegram timed out")
            return False

        inc.drain_critical_alert(failing_send)
        kinds = [c.get("kind") for k, c in inc._load().items()
                 if not k.startswith("_") and isinstance(c, dict)]
        self.assertIn("delivery_failure", kinds)  # survived the drain's save

    def test_daily_cap_drops_without_sending(self):
        # Simulate the day's attempts already exhausted: a new severe failure queues, but
        # drain must NOT send (it drops the flag; the incident is still in the ledger).
        inc.record_incident("circuit_open", "anthropic")
        today = inc._now_iso()[:10]
        inc.set_meta(inc._CRITICAL_ALERT_KEY, {
            "pending": self._pending(), "date": today,
            "attempts_today": inc.MAX_CRITICAL_ALERTS_PER_DAY,
        })
        sent = []
        inc.drain_critical_alert(lambda t: sent.append(t) or True)
        self.assertEqual(sent, [])           # capped: no send
        self.assertIsNone(self._pending())   # but dropped so it can't accumulate

    def test_success_does_not_clear_a_superseding_alert(self):
        inc.record_incident("circuit_open", "anthropic")  # alert A is pending

        def send_then_supersede(text):
            # a newer severe incident lands during the (mocked) Telegram I/O
            inc.record_incident("budget_exceeded", "daily call cap reached")  # alert B
            return True

        out = inc.drain_critical_alert(send_then_supersede)
        self.assertIsNotNone(out)                       # A was delivered
        p = self._pending()
        self.assertIsNotNone(p)                         # B was NOT dropped
        self.assertEqual(p["kind"], "budget_exceeded")  # the newer alert survives for next tick

    def test_failed_send_keeps_pending_for_retry_then_attempt_capped(self):
        inc.record_incident("circuit_open", "anthropic")
        # A failed delivery must NOT lose the alert: pending is kept for the next tick.
        out = inc.drain_critical_alert(lambda t: False)
        self.assertIsNone(out)                       # nothing delivered
        self.assertIsNotNone(self._pending())        # retained for retry
        alert = inc.get_meta(inc._CRITICAL_ALERT_KEY, {})
        self.assertEqual(alert.get("attempts_today"), 1)  # the attempt is counted (bounded)
        # Repeated failures are bounded by the daily attempt cap, then dropped.
        for _ in range(inc.MAX_CRITICAL_ALERTS_PER_DAY + 1):
            inc.drain_critical_alert(lambda t: False)
        self.assertIsNone(self._pending())           # eventually dropped, not retried forever


class LocalLoopDrainWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH",
                                            Path(self.tmp) / "inc.json"))

    def test_local_loop_drain_uses_send_telegram(self):
        inc.record_incident("circuit_open", "anthropic")
        fake = mock.MagicMock(return_value=True)
        with mock.patch("send_telegram.try_send_message", fake):
            sched._drain_critical_alerts()
        self.assertEqual(fake.call_count, 1)
        self.assertIn("circuit", fake.call_args[0][0].lower())


if __name__ == "__main__":
    unittest.main()
