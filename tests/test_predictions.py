"""Prediction-scoring tests (argo_predictions): the H0.3-H0.5 loop.

A prediction is recorded UNARMED, armed at merge time (the clock starts at deploy),
and scored only once due -- then it moves the world-model belief by the +-0.20
prediction step, the strongest legitimate confidence mover. An unknown metric is
never fabricated: it stays unscored. Pure + hermetic (tmp stores, no network).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_cost as cost
import argo_incidents as inc
import argo_predictions as pred
import argo_store
import world_model as wm


class PredictionsTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.enterContext(mock.patch.object(wm, "WORLD_MODEL_PATH", base / "wm.json"))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.wm_id = wm.add_belief("Adopting x improves Argo")  # seed 0.30

    def _record(self, metric, days=14):
        return pred.record(self.wm_id, "claim text", metric, days, source="test")

    def test_unarmed_is_never_scored(self):
        self._record({"kind": "incident_absent", "key": "tool_error|x"})
        self.assertEqual(pred.score_due(), [])

    def test_not_due_is_never_scored(self):
        pid = self._record({"kind": "incident_absent", "key": "tool_error|x"})
        pred.arm(pid)  # armed now -> due in 14 days
        self.assertEqual(pred.score_due(), [])

    def test_arm_sets_due_from_merge_time_and_is_idempotent(self):
        pid = self._record({"kind": "incident_absent", "key": "tool_error|x"})
        p = pred.arm(pid, "2026-06-01T00:00:00Z")
        self.assertEqual(p["due"], "2026-06-15T00:00:00Z")
        p2 = pred.arm(pid, "2026-06-09T00:00:00Z")  # re-arm: no-op
        self.assertEqual(p2["due"], "2026-06-15T00:00:00Z")

    def test_held_prediction_moves_confidence_up(self):
        pid = self._record({"kind": "incident_absent", "key": "tool_error|never"})
        pred.arm(pid, "2026-05-01T00:00:00Z")  # due 2026-05-15, long past
        lines = []
        scored = pred.score_due(notify=lines.append)
        self.assertEqual(len(scored), 1)
        self.assertTrue(scored[0]["correct"])
        belief = wm.get_belief(self.wm_id)
        self.assertAlmostEqual(belief["confidence"], 0.50)  # 0.30 + 0.20
        self.assertEqual(belief["status"], "active")        # earned out of unverified
        self.assertEqual(len(lines), 1)
        self.assertIn("held", lines[0])

    def test_failed_prediction_moves_confidence_down(self):
        pid = self._record({"kind": "incident_absent",
                            "incident_kind": "scheduler_task_error"})
        pred.arm(pid, "2026-05-01T00:00:00Z")
        inc.record_incident("scheduler_task_error", "boom")  # recurred after arming
        scored = pred.score_due()
        self.assertEqual(len(scored), 1)
        self.assertFalse(scored[0]["correct"])
        belief = wm.get_belief(self.wm_id)
        self.assertAlmostEqual(belief["confidence"], 0.10)  # 0.30 - 0.20
        self.assertTrue(any(scored[0]["id"] in r for r in belief["refutations"]))

    def test_unknown_metric_left_unscored_never_fabricated(self):
        pid = self._record({"kind": "tokens_cheaper"})
        pred.arm(pid, "2026-05-01T00:00:00Z")
        self.assertEqual(pred.score_due(), [])
        p = pred.get_prediction(pid)
        self.assertIsNone(p["scored_at"])
        self.assertIsNone(p["correct"])

    def test_scoring_is_once_only(self):
        pid = self._record({"kind": "incident_absent", "key": "tool_error|never"})
        pred.arm(pid, "2026-05-01T00:00:00Z")
        self.assertEqual(len(pred.score_due()), 1)
        self.assertEqual(pred.score_due(), [])


class CostPredictionTest(unittest.TestCase):
    """The `cache_ratio` metric kind: the first MEASURED cost claim. Synthetic
    ledger + predictions store in tmp; no network, no real data."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.enterContext(mock.patch.object(wm, "WORLD_MODEL_PATH", base / "wm.json"))
        self.ledger = base / "cost.json"
        self.enterContext(mock.patch.object(cost, "LEDGER_PATH", self.ledger))
        self.wm_id = wm.add_belief("Caching cuts chat input")  # seed 0.30

    def _ledger(self, rows):
        argo_store.save_json(self.ledger, rows)

    def _row(self, inp, cache_read, ts=2_000_000_000.0, label="chat/claude-opus-4-8"):
        # ts well after the 2026-05-01 arm date used below, so it falls in-window.
        return {"ts": ts, "model": "claude-opus-4-8", "provider": "anthropic",
                "label": label, "input_tokens": inp, "output_tokens": 10,
                "cache_creation_tokens": 0, "cache_read_tokens": cache_read}

    def _armed_cache_pred(self, min_ratio=0.50, min_calls=3):
        pid = pred.record(self.wm_id, "caching cuts chat input >= 50%",
                          {"kind": "cache_ratio", "min_ratio": min_ratio,
                           "min_calls": min_calls, "label_prefix": "chat/"},
                          14, source="test")
        pred.arm(pid, "2026-05-01T00:00:00Z")  # due 2026-05-15, long past
        return pid

    def test_scores_correct_when_ledger_meets_claim(self):
        # 90% of billable input served from cache, 3 calls >= min_calls=3.
        self._ledger([self._row(100, 900) for _ in range(3)])
        self._armed_cache_pred()
        lines = []
        scored = pred.score_due(notify=lines.append)
        self.assertEqual(len(scored), 1)
        self.assertTrue(scored[0]["correct"])
        belief = wm.get_belief(self.wm_id)
        self.assertAlmostEqual(belief["confidence"], 0.50)  # 0.30 + 0.20
        self.assertEqual(belief["status"], "active")
        self.assertIn("held", lines[0])

    def test_scores_incorrect_when_claim_not_met(self):
        # Only 10% from cache, plenty of calls -> the claim fails, belief drops.
        self._ledger([self._row(900, 100) for _ in range(5)])
        self._armed_cache_pred()
        scored = pred.score_due()
        self.assertEqual(len(scored), 1)
        self.assertFalse(scored[0]["correct"])
        belief = wm.get_belief(self.wm_id)
        self.assertAlmostEqual(belief["confidence"], 0.10)  # 0.30 - 0.20
        self.assertTrue(any(scored[0]["id"] in r for r in belief["refutations"]))

    def test_insufficient_data_stays_unscored_not_guessed(self):
        # Empty ledger: nothing to measure -> abstain (unscored), NOT a guessed pass.
        self._ledger([])
        pid = self._armed_cache_pred(min_calls=3)
        self.assertEqual(pred.score_due(), [])
        p = pred.get_prediction(pid)
        self.assertIsNone(p["scored_at"])
        self.assertIsNone(p["correct"])
        belief = wm.get_belief(self.wm_id)
        self.assertAlmostEqual(belief["confidence"], 0.30)  # untouched

    def test_too_few_calls_stays_unscored(self):
        # Ratio would PASS (90%) but only 2 calls < min_calls=3 -> still abstain.
        self._ledger([self._row(100, 900) for _ in range(2)])
        pid = self._armed_cache_pred(min_calls=3)
        self.assertEqual(pred.score_due(), [])
        self.assertIsNone(pred.get_prediction(pid)["correct"])

    def test_out_of_window_rows_excluded(self):
        # Rows logged BEFORE arming (pre-caching baseline) must not count.
        self._ledger([self._row(100, 900, ts=1_000.0) for _ in range(5)])  # 1970
        pid = self._armed_cache_pred(min_calls=3)  # armed 2026-05-01
        self.assertEqual(pred.score_due(), [])  # no in-window rows -> abstain
        self.assertIsNone(pred.get_prediction(pid)["correct"])

    def test_arm_cost_prediction_helper_records_and_arms(self):
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH",
                                            self.ledger.parent / "inc.json"))
        pid = pred.arm_cost_prediction(min_ratio=0.6, days=7, min_calls=10)
        p = pred.get_prediction(pid)
        self.assertIsNotNone(p["armed_at"])  # clock started now
        self.assertEqual(p["metric"]["kind"], "cache_ratio")
        self.assertEqual(p["metric"]["min_ratio"], 0.6)
        self.assertEqual(p["metric"]["label_prefix"], "chat/")
        # The bound belief exists and is unverified (must earn its way up).
        self.assertIsNotNone(wm.get_belief(p["belief_id"]))
        # Fresh, due-in-7-days prediction with no ledger yet scores nothing.
        self.assertEqual(pred.score_due(), [])


if __name__ == "__main__":
    unittest.main()
