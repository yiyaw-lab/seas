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

import argo_incidents as inc
import argo_predictions as pred
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


if __name__ == "__main__":
    unittest.main()
