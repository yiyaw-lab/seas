"""Rehearse panel-diversity eval -- offline correctness proof. PURE: a deterministic
fake provider is installed (rehearse_panel_eval's own --mock path), so the WHOLE
pipeline (both arms -> independent scorer -> risk coverage -> diverse-vs-same) runs
with ZERO network and ZERO spend. Proves the harness implements the pinned study
without any paid model call.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import unittest
from unittest import mock

import rehearse_panel_eval as eval_mod


class PanelDiversityEvalOfflineTest(unittest.TestCase):
    """Both arms run, coverage is computed per arm, and the diverse-vs-same binary
    result is produced -- all under the deterministic mock, no paid call."""

    def _run(self, n=1):
        # clear=True so no ambient real API key leaks in; the mock injects its own
        # fake keys internally and restores them. ARGO_MODEL unset -> resolve_models
        # falls back to DEFAULT_MODELS, but the three preferred adversary models
        # (claude-sonnet/gpt-5/grok) drive the assignment regardless.
        with mock.patch.dict(os.environ, {}, clear=True):
            return eval_mod.run_eval(eval_mod.BETS[:n], mock=True)

    def test_both_arms_run_to_completion(self):
        result = self._run(n=2)
        self.assertIsNotNone(result)  # not aborted (mock supplies fake keys)
        self.assertEqual(len(result["per_bet"]), 2)
        for row in result["per_bet"]:
            self.assertIn("diverse", row)  # diverse-3 arm ran
            self.assertIn("same", row)     # same-3 negative-control arm ran

    def test_risk_coverage_computed_for_each_arm(self):
        result = self._run(n=2)
        for row in result["per_bet"]:
            for arm in ("diverse", "same"):
                cov = row[arm]["coverage"]
                self.assertIsInstance(cov, float)
                self.assertGreaterEqual(cov, 0.0)
                self.assertLessEqual(cov, 1.0)  # a FRACTION, not a count
                # covered indices are a subset of the per-bet held-out risk set
                self.assertTrue(all(isinstance(i, int) for i in row[arm]["covered"]))

    def test_diverse_vs_same_comparison_produced(self):
        result = self._run(n=3)
        # the binary done-check structure exists...
        self.assertIn("diverse_avg", result)
        self.assertIn("same_avg", result)
        self.assertIn("diverse_beats_same", result)
        self.assertIsInstance(result["diverse_beats_same"], bool)
        # ...and matches the averages it summarizes.
        self.assertEqual(result["diverse_beats_same"],
                         result["diverse_avg"] > result["same_avg"])

    def test_diverse_arm_is_three_distinct_models_same_arm_is_one(self):
        # The same-3 arm collapses all roles onto ONE model; the diverse arm keeps
        # three distinct (the negative control vs the treatment).
        with mock.patch.dict(os.environ, {}, clear=True):
            restore = eval_mod._install_mock_provider({"bet": None})
            try:
                diverse = eval_mod.assign_diverse(eval_mod.ROLES)
                same = eval_mod.assign_same(eval_mod.ROLES)
            finally:
                restore()
        self.assertEqual(len(set(diverse.values())), 3)  # diverse minds
        self.assertEqual(len(set(same.values())), 1)      # one model x3

    def test_mock_is_deterministic_and_diverse_beats_same(self):
        # Stability: identical inputs -> identical coverage (no salted hash, no RNG),
        # and the deterministic fixture yields the expected DIVERSE>same direction so
        # the harness's binary output is exercised on a real positive result.
        r1 = self._run(n=3)
        r2 = self._run(n=3)
        self.assertEqual(r1["diverse_avg"], r2["diverse_avg"])
        self.assertEqual(r1["same_avg"], r2["same_avg"])
        self.assertTrue(r1["diverse_beats_same"])

    def test_risk_coverage_scorer_marks_against_a_synthetic_risk_set(self):
        # Exercise the coverage scorer directly with a FIXED synthetic risk set and a
        # critique that embeds the scorer's RISK# markers for two of the three risks.
        # Proves coverage = covered/total against a supplied held-out set, no model call.
        restore = eval_mod._install_mock_provider({"bet": None})
        try:
            risk_set = ["risk alpha", "risk beta", "risk gamma"]
            critique = "[critic/x] RISK#1: risk alpha RISK#3: risk gamma"
            cov, covered = eval_mod.risk_coverage(critique, risk_set)
        finally:
            restore()
        self.assertEqual(covered, [1, 3])
        self.assertAlmostEqual(cov, 2 / 3)


if __name__ == "__main__":
    unittest.main()
