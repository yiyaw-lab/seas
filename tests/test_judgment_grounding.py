"""Judgment-grounding tests: the Rehearse verdict -> belief -> scored-prediction loop.

The #1 cofounder move (docs/plans/2026-06-18-argo-cofounder-strategy.md): wire the
verified belief graph + prediction grader into the BUILD decision, not only the
self-evolution flywheel. A SHIP/REVISE verdict records a dated, belief-bound
prediction; SELECT arms it (the clock starts when the user commits); a human
SHIPPED/DROPPED reply grades the outcome; the existing daily score_due run moves
the per-verdict-class belief +-0.20. A KILL records nothing -- the gate refused the
bet, so there is no ship outcome to grade. Pure + hermetic (tmp stores, no network).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_predictions as pred
import argo_rating
import argo_rehearse as reh
import argo_store
import world_model as wm


class JudgmentGroundingTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.projects = base / "projects.json"
        self.bp = reh.ROOT / "argo" / "rehearsals" / "P-001.md"  # under ROOT for relative_to
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.enterContext(mock.patch.object(pred, "PROJECTS_LOG", self.projects))
        self.enterContext(mock.patch.object(wm, "WORLD_MODEL_PATH", base / "wm.json"))
        self.enterContext(mock.patch.object(reh, "PROJECTS_LOG", self.projects))

    def _seed_project(self, **extra):
        entry = {"id": "P-001", "text": "build a thing"}
        entry.update(extra)
        argo_store.save_json(self.projects, [entry])

    def _entry(self):
        return argo_store.load_json(self.projects, [])[0]

    # --- metric kind: project_shipped is True/False/None, never fabricated ----
    def test_project_shipped_metric_kind(self):
        metric = {"kind": "project_shipped", "project_id": "P-001"}
        self._seed_project(shipped=True)
        self.assertTrue(pred._evaluate(metric, "2026-01-01T00:00:00Z"))
        self._seed_project(dropped=True)
        self.assertFalse(pred._evaluate(metric, "2026-01-01T00:00:00Z"))
        self._seed_project()  # neither marker -> unknown, leave unscored
        self.assertIsNone(pred._evaluate(metric, "2026-01-01T00:00:00Z"))
        # project vanished -> unknown, never a fabricated False
        self.assertIsNone(
            pred._evaluate({"kind": "project_shipped", "project_id": "P-999"},
                           "2026-01-01T00:00:00Z"))

    # --- rehearse records on SHIP, records nothing on KILL --------------------
    def test_ship_records_unarmed_prediction_and_belief(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        pid = self._entry().get("judgment_prediction_id")
        self.assertIsNotNone(pid)
        p = pred.get_prediction(pid)
        self.assertEqual(p["metric"], {"kind": "project_shipped", "project_id": "P-001"})
        self.assertIsNone(p["armed_at"])  # not selected -> clock not started
        beliefs = wm.get_beliefs()
        self.assertEqual(len(beliefs), 1)
        self.assertIn("SHIP", beliefs[0]["claim"])

    def test_kill_records_nothing(self):
        self._seed_project()
        reh._stamp_project("P-001", "KILL", self.bp)
        self.assertIsNone(self._entry().get("judgment_prediction_id"))
        self.assertEqual(pred._load(), [])
        self.assertEqual(wm.get_beliefs(), [])

    def test_ship_on_already_selected_project_arms_immediately(self):
        self._seed_project(selected=True)  # SELECT path rehearses AFTER marking selected
        reh._stamp_project("P-001", "SHIP", self.bp)
        pid = self._entry()["judgment_prediction_id"]
        self.assertIsNotNone(pred.get_prediction(pid)["armed_at"])

    # --- standalone REHEARSE then SELECT: SELECT arms the existing prediction --
    def test_select_arms_existing_prediction(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)  # unarmed (not selected)
        self.assertIsNone(pred.get_prediction(self._entry()["judgment_prediction_id"])["armed_at"])
        argo_rating.select_latest_project(self.projects)
        pid = self._entry()["judgment_prediction_id"]
        self.assertIsNotNone(pred.get_prediction(pid)["armed_at"])

    # --- end to end: human grades the outcome, score_due moves the belief -----
    def test_shipped_outcome_scores_belief_up(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        argo_rating.select_latest_project(self.projects)  # arms it
        items = pred._load()  # backdate arming so the prediction is due
        items[0]["armed_at"] = "2026-05-01T00:00:00Z"
        items[0]["due"] = "2026-05-15T00:00:00Z"
        pred._save(items)
        self.assertEqual(argo_rating.set_project_outcome(self.projects, True),
                         ("P-001", "pending"))
        scored = pred.score_due()
        self.assertEqual(len(scored), 1)
        self.assertTrue(scored[0]["correct"])
        belief = wm.get_beliefs()[0]
        self.assertAlmostEqual(belief["confidence"], 0.50)  # 0.30 seed + 0.20 prediction

    # --- review fix: outcome targets the SELECTED bet, not last-shown -------
    def test_outcome_targets_selected_not_last_shown(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        argo_rating.select_latest_project(self.projects)  # P-001 selected + armed
        log = argo_store.load_json(self.projects, [])      # newer SHOWN candidate
        log.append({"id": "P-002", "text": "another idea", "shown_at": "2999-01-01 00:00 UTC"})
        argo_store.save_json(self.projects, log)
        pid, state = argo_rating.set_project_outcome(self.projects, True)  # bare SHIPPED
        self.assertEqual(pid, "P-001")  # the committed bet, not last-shown P-002
        self.assertEqual(state, "pending")
        rows = argo_store.load_json(self.projects, [])
        self.assertTrue(rows[0]["shipped"])           # P-001 graded
        self.assertNotIn("shipped", rows[1])          # P-002 untouched

    # --- review fix: don't claim a grade when no prediction is bound --------
    def test_outcome_on_unrehearsed_project_reports_not_graded(self):
        self._seed_project()  # never selected/rehearsed -> no judgment prediction
        pid, state = argo_rating.set_project_outcome(self.projects, True, "P-001")
        self.assertEqual(pid, "P-001")
        self.assertEqual(state, "none")

    def test_bare_outcome_with_nothing_selected_returns_none(self):
        self._seed_project(shown_at="2026-01-01 00:00 UTC")  # shown, never selected
        self.assertEqual(argo_rating.set_project_outcome(self.projects, True),
                         (None, "none"))

    # --- round-2 fix: a correction AFTER scoring is locked, never re-claimed --
    def test_correction_after_scoring_locks_grade(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        argo_rating.select_latest_project(self.projects)
        items = pred._load()
        items[0]["armed_at"] = "2026-05-01T00:00:00Z"
        items[0]["due"] = "2026-05-15T00:00:00Z"
        pred._save(items)
        argo_rating.set_project_outcome(self.projects, True)     # shipped
        self.assertEqual(len(pred.score_due()), 1)               # graded -> 0.50
        self.assertAlmostEqual(wm.get_beliefs()[0]["confidence"], 0.50)
        pid, state = argo_rating.set_project_outcome(self.projects, False)  # correction
        self.assertEqual((pid, state), ("P-001", "scored"))      # honest: not re-graded
        self.assertEqual(pred.score_due(), [])                   # locked, skipped
        self.assertAlmostEqual(wm.get_beliefs()[0]["confidence"], 0.50)  # belief unchanged
        self.assertTrue(self._entry()["dropped"])                # log reflects correction

    # --- round-2 fix: a SHIP->REVISE flip voids the old pred and rebinds belief
    def test_verdict_flip_ship_to_revise_rebinds_belief(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        ship_pred = self._entry()["judgment_prediction_id"]
        ship_belief = pred.get_prediction(ship_pred)["belief_id"]
        reh._stamp_project("P-001", "REVISE", self.bp)           # verdict flips
        new_pred = self._entry()["judgment_prediction_id"]
        self.assertNotEqual(new_pred, ship_pred)                 # old retired, new recorded
        self.assertTrue(pred.get_prediction(ship_pred)["voided"])
        new_belief = pred.get_prediction(new_pred)["belief_id"]
        self.assertNotEqual(new_belief, ship_belief)             # bound to the REVISE belief
        self.assertIn("REVISE", wm.get_belief(new_belief)["claim"])
        self.assertEqual(self._entry()["judgment_verdict"], "REVISE")

    # --- review fix: a re-rehearsal to KILL voids the armed prediction ------
    def test_kill_re_rehearsal_voids_armed_prediction(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        argo_rating.select_latest_project(self.projects)  # armed
        pred_id = self._entry()["judgment_prediction_id"]
        self.assertIsNotNone(pred.get_prediction(pred_id)["armed_at"])
        reh._stamp_project("P-001", "KILL", self.bp)       # verdict flips
        self.assertNotIn("judgment_prediction_id", self._entry())
        self.assertTrue(pred.get_prediction(pred_id)["voided"])
        # even if the bet is later marked shipped, the voided pred never scores
        items = pred._load()
        items[0]["armed_at"] = "2026-05-01T00:00:00Z"
        items[0]["due"] = "2026-05-15T00:00:00Z"
        pred._save(items)
        argo_rating.set_project_outcome(self.projects, True, "P-001")
        self.assertEqual(pred.score_due(), [])
        self.assertAlmostEqual(wm.get_beliefs()[0]["confidence"], 0.30)  # belief untouched

    # --- round-3 fix: outcome re-arms a bound-but-unarmed prediction ---------
    def test_outcome_arms_a_bound_unarmed_prediction(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)  # recorded UNARMED (not selected)
        pred_id = self._entry()["judgment_prediction_id"]
        self.assertIsNone(pred.get_prediction(pred_id)["armed_at"])
        log = argo_store.load_json(self.projects, [])  # selected, but arm was lost
        log[0]["selected_at"] = "2026-06-18 10:00 UTC"
        argo_store.save_json(self.projects, log)
        pid, state = argo_rating.set_project_outcome(self.projects, True)
        self.assertEqual((pid, state), ("P-001", "pending"))
        self.assertIsNotNone(pred.get_prediction(pred_id)["armed_at"])  # recovered

    # --- round-3 fix: a failed void keeps ONE binding (no double-count) -------
    def test_verdict_flip_with_void_failure_keeps_single_binding(self):
        self._seed_project()
        reh._stamp_project("P-001", "SHIP", self.bp)
        ship_pred = self._entry()["judgment_prediction_id"]
        with mock.patch.object(pred, "cancel", side_effect=OSError("disk")):
            reh._stamp_project("P-001", "REVISE", self.bp)  # void fails
        self.assertEqual(self._entry()["judgment_prediction_id"], ship_pred)  # kept
        self.assertEqual(len(pred._load()), 1)  # no duplicate recorded

    # --- round-3 fix: tie-break targets the later of two SELECTed bets --------
    def test_outcome_targets_later_of_two_selected(self):
        argo_store.save_json(self.projects, [
            {"id": "P-001", "text": "a", "selected_at": "2026-06-18 10:00 UTC"},
            {"id": "P-002", "text": "b", "selected_at": "2026-06-18 10:00 UTC"},
        ])
        pid, _ = argo_rating.set_project_outcome(self.projects, True)
        self.assertEqual(pid, "P-002")  # same minute -> later log entry wins

    def test_dropped_outcome_scores_belief_down(self):
        self._seed_project()
        reh._stamp_project("P-001", "REVISE", self.bp)
        argo_rating.select_latest_project(self.projects)
        items = pred._load()
        items[0]["armed_at"] = "2026-05-01T00:00:00Z"
        items[0]["due"] = "2026-05-15T00:00:00Z"
        pred._save(items)
        argo_rating.set_project_outcome(self.projects, False)  # DROPPED
        scored = pred.score_due()
        self.assertEqual(len(scored), 1)
        self.assertFalse(scored[0]["correct"])
        belief = wm.get_beliefs()[0]
        self.assertAlmostEqual(belief["confidence"], 0.10)  # 0.30 - 0.20


if __name__ == "__main__":
    unittest.main()
