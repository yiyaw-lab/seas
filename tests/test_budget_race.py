"""DailyBudget.check_and_increment() TOCTOU-race test -- Bugbot finding on PR #84:
the read-check-increment sequence ran unsynchronized, so N concurrent webhook
threads could all read cost_today_usd()/the on-disk count as under-cap before any
of their usage/count landed, overshooting DAILY_COST_CAP_USD / DAILY_CALL_CAP.
Fix: a module-level threading.Lock (argo_guard._budget_lock) now serializes the
whole check_and_increment() body.

Deterministic, non-flaky: patches argo_cost.cost_today_usd with a slow read (a
short sleep between reading a shared counter and returning) so an unserialized
version reliably interleaves two threads through the check before either
increments; the lock must force strict alternation (thread A's full
check-and-increment completes before thread B's starts, or vice versa) so the
returned counts are always {1, 2} in SOME order, never a repeat.

Pure: budget_path a tmp file; no network/key/real data.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import argo_cost
import argo_guard


class BudgetRaceTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.budget_path = tmp / "argo_budget.json"

    def test_concurrent_check_and_increment_is_serialized(self):
        # A slow cost_today_usd (mimics a real ledger scan) widens the race window:
        # without the lock, two threads both read the on-disk count as 0 during the
        # sleep and both write count=1 (the TOCTOU symptom). With the lock, thread
        # B's read of state can only start after thread A's write completes, so the
        # two calls must land distinct, sequential counts.
        def slow_cost_today_usd(now=None):
            time.sleep(0.05)
            return 0.0  # always under cap; this test targets the count-cap race

        budget = argo_guard.DailyBudget(path=self.budget_path, cap=500, cost_cap=20.0)
        results = []
        errors = []

        def worker():
            try:
                results.append(budget.check_and_increment())
            except Exception as exc:  # pragma: no cover - failure path surfaced via errors
                errors.append(exc)

        # Patch once, OUTSIDE the threads: unittest.mock.patch's save/restore of
        # the original attribute is not itself thread-safe, so applying it inside
        # each worker races the two threads' __enter__/__exit__ against each
        # other and can permanently corrupt argo_cost.cost_today_usd for every
        # later test in the process (observed while developing this test).
        with mock.patch.object(argo_cost, "cost_today_usd", slow_cost_today_usd):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(errors, [])
        # Serialized: the two calls must land distinct, sequential counts (1, 2) in
        # some order -- never both reading count==0 and both writing count=1.
        self.assertEqual(sorted(results), [1, 2])

    def test_lock_prevents_concurrent_entry_into_check_and_increment(self):
        # Direct proof the lock serializes: instrument entry/exit and assert no
        # overlap, with an artificial delay inside the critical section forcing a
        # thread that would otherwise interleave to block instead.
        entries = []
        real_check_cost = argo_guard.DailyBudget._check_cost

        def slow_check_cost(self, today):
            entries.append(("enter", time.monotonic()))
            time.sleep(0.05)
            entries.append(("exit", time.monotonic()))
            return real_check_cost(self, today)

        budget = argo_guard.DailyBudget(path=self.budget_path, cap=500, cost_cap=20.0)

        with mock.patch.object(argo_cost, "cost_today_usd", lambda now=None: 0.0):
            with mock.patch.object(argo_guard.DailyBudget, "_check_cost", slow_check_cost):
                threads = [threading.Thread(target=budget.check_and_increment)
                          for _ in range(3)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

        # Reconstruct enter/exit pairs and verify no two overlap.
        self.assertEqual(len(entries), 6)
        intervals = list(zip(entries[0::2], entries[1::2]))
        for i, (enter_i, exit_i) in enumerate(intervals):
            for j, (enter_j, exit_j) in enumerate(intervals):
                if i == j:
                    continue
                overlap = enter_i[1] < exit_j[1] and enter_j[1] < exit_i[1]
                self.assertFalse(overlap, "two threads held the critical section at once")


if __name__ == "__main__":
    unittest.main()
