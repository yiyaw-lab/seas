"""DailyBudget cost-cap tests -- DAILY_COST_CAP_USD (argo_guard.py), the second
half of the daily budget alongside the existing flat DAILY_CALL_CAP. A premium
model (claude-fable-5: $10/$50 per MTok) can blow a cost budget well under 500
calls, so check_and_increment() now also reads today's estimated spend off
argo_cost's ledger (argo_cost.cost_today_usd(), itself built on
argo_cost.PRICING_USD_PER_MTOK + estimate_cost_usd()) and raises BudgetExceeded
once that estimate reaches the cap -- same failure mode as the call cap.

Pure: LEDGER_PATH and DailyBudget.path are both patched to tmp files, so no
network/LLM/real data/*.json.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import argo_cost
import argo_guard


class _Usage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Resp:
    def __init__(self, usage):
        self.usage = usage


class DailyCostCapTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ledger_path = tmp / "argo_cost_ledger.json"
        self.budget_path = tmp / "argo_budget.json"
        self.enterContext(mock.patch.object(argo_cost, "LEDGER_PATH", self.ledger_path))

    def _record(self, model, input_tokens, output_tokens, ts=None):
        argo_cost.record_usage(
            _Resp(_Usage(input_tokens=input_tokens, output_tokens=output_tokens)),
            model, "anthropic", f"chat/{model}", ts=ts)

    def test_cost_cap_trips_after_enough_spend(self):
        # claude-fable-5: $10/MTok in, $50/MTok out. 100k output tokens = $5; two
        # such calls = $10, still under a $8 cap after the first, over after the
        # second -- pick a cap that the FIRST call alone already exceeds so the
        # trip is unambiguous.
        budget = argo_guard.DailyBudget(path=self.budget_path, cap=500, cost_cap=1.0)
        now = time.time()
        # $2 worth: 40k output tokens * $50/MTok = $2.00
        self._record("claude-fable-5", input_tokens=0, output_tokens=40_000, ts=now)
        with self.assertRaises(argo_guard.DailyBudget.BudgetExceeded):
            budget.check_and_increment()

    def test_cost_cap_not_tripped_under_cap(self):
        budget = argo_guard.DailyBudget(path=self.budget_path, cap=500, cost_cap=20.0)
        now = time.time()
        self._record("claude-haiku-4-5", input_tokens=10_000, output_tokens=1_000, ts=now)
        # haiku: $1/MTok in, $5/MTok out -> 10k in = $0.01, 1k out = $0.005 ~= $0.015
        count = budget.check_and_increment()
        self.assertEqual(count, 1)

    def test_cost_resets_across_days(self):
        budget = argo_guard.DailyBudget(path=self.budget_path, cap=500, cost_cap=1.0)
        yesterday = time.time() - 86400
        # $5 worth yesterday -- would trip a $1 cap if counted, but cost_today_usd
        # only sums today's rows.
        self._record("claude-fable-5", input_tokens=0, output_tokens=100_000, ts=yesterday)
        count = budget.check_and_increment()  # must NOT raise
        self.assertEqual(count, 1)

    def test_default_cap_is_env_overridable(self):
        with mock.patch.dict("os.environ", {"ARGO_DAILY_COST_CAP": "42.5"}):
            import importlib
            import argo_guard as g
            importlib.reload(g)
            try:
                self.assertEqual(g.DAILY_COST_CAP_USD, 42.5)
            finally:
                importlib.reload(g)  # restore the un-overridden module state


if __name__ == "__main__":
    unittest.main()
