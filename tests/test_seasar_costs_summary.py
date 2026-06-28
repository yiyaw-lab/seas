"""Negative-control test for the `--costs` operator CLI summary
(`seasar_compile._print_costs_summary`).

The cost ledger is a JSONL file appended to non-atomically (`COSTS_PATH.open("a")`
+ plain write), so a process killed mid-write can leave a truncated final line.
Pre-fix, the summary read it with `[json.loads(l) for l in ...]`, so one bad line
raised `json.JSONDecodeError` and crashed the whole `--costs` report. This pins
that a truncated line, a non-dict JSON line, and a legacy record missing the cost
fields are all tolerated -- the summary skips/degrades, never crashes.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import seasar_compile as sc


class PrintCostsSummaryRobustness(unittest.TestCase):
    def setUp(self):
        self._orig = sc.COSTS_PATH
        self.tmp = tempfile.mkdtemp()
        sc.COSTS_PATH = Path(self.tmp) / "costs.jsonl"

    def tearDown(self):
        sc.COSTS_PATH = self._orig

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sc._print_costs_summary()  # must never raise
        return buf.getvalue()

    def test_truncated_and_legacy_lines_do_not_crash(self):
        good = json.dumps({"id": "o1", "total_cost_usd": 0.5,
                           "total_input_tokens": 100, "total_output_tokens": 200})
        sc.COSTS_PATH.write_text(
            good + "\n"
            + '{"id": "o2", "total_cost_usd": 1.5, "total_input_to'  # truncated mid-write
            + "\n"
            + "42\n"                                  # valid JSON, not a dict
            + json.dumps({"id": "o3"}) + "\n"         # legacy record: no cost fields
        )
        out = self._run()
        # o1 + the legacy o3 survive as dicts; the truncated line and the bare int
        # are skipped. The legacy record contributes 0 to the totals via .get().
        self.assertIn("2 build orders", out)
        self.assertIn("total $0.50", out)

    def test_null_totals_degrade_to_zero(self):
        sc.COSTS_PATH.write_text(json.dumps({"id": "o1", "total_cost_usd": None,
                                             "total_input_tokens": None,
                                             "total_output_tokens": None}) + "\n")
        out = self._run()
        self.assertIn("1 build orders", out)
        self.assertIn("total $0.00", out)

    def test_all_lines_corrupt_reports_empty(self):
        sc.COSTS_PATH.write_text('{"broken\nalso-not-json\n')
        self.assertIn("cost ledger empty", self._run())


if __name__ == "__main__":
    unittest.main()
