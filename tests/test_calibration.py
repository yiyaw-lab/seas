"""Calibration tests: the per-verdict-class "how reliable is my SHIP call FOR YOU"
number (argo_calibration) and its surfaces (argo_rehearse._summary_line,
argo_self.gather_performance).

Move #3 of the judgment-grounding work (docs/plans/2026-06-18-argo-cofounder-strategy):
read the graded Rehearse outcomes back as one legible number, with two honesty rails
enforced IN CODE -- an n-floor (a too-thin verdict class is omitted entirely) and honest
abstention (an ungraded bet is excluded, never a miss). Pure + hermetic.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_calibration as cal
import argo_predictions as pred
import argo_rehearse as reh
import argo_self as self_mod
import argo_store


N = cal.CALIBRATION_MIN_N  # the n-floor, referenced so tests track the constant
HI = pred.MATTERED_ENERGY_MIN       # at/above this = mattered (full credit)
LO = pred.MATTERED_ENERGY_MIN - 1   # below = shipped but did not matter (half credit)


def _bet(i, verdict="SHIP", shipped=True, energy=None, dropped=False, selected=True):
    """A committed, graded project-log entry, shaped like a real one after rehearse +
    SELECT + a SHIPPED/DROPPED grade + an energy rating."""
    e = {"id": f"P-{i:03d}", "text": "a bet", "verdict": verdict}
    if verdict in ("SHIP", "REVISE"):
        e["judgment_verdict"] = verdict  # the grading-class flag calibration keys on
    if selected:
        e["selected_at"] = "2026-06-01 10:00 UTC"
    if shipped:
        e["shipped"] = True
    if dropped:
        e["dropped"] = True
    if energy is not None:
        e["energy"] = energy
    return e


class ComputeCalibrationTest(unittest.TestCase):
    def test_below_floor_is_omitted_entirely(self):
        # n-floor IN CODE: one short of the floor -> the class is absent, not caveated.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N - 1)]
        self.assertEqual(cal.compute_calibration(bets), {})

    def test_at_floor_all_shipped_and_mattered(self):
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]
        c = cal.compute_calibration(bets)["SHIP"]
        self.assertEqual(c["n"], N)
        self.assertEqual(c["shipped"], N)
        self.assertEqual(c["rate"], 1.0)
        self.assertEqual(c["score"], 1.0)

    def test_partial_credit_shipped_low_energy_and_dropped(self):
        # N=4 mix: 2 shipped+high (1.0 each), 1 shipped+low (0.5), 1 dropped (0.0).
        bets = [
            _bet(1, shipped=True, energy=HI),
            _bet(2, shipped=True, energy=HI),
            _bet(3, shipped=True, energy=LO),
            _bet(4, shipped=False, dropped=True),
        ]
        c = cal.compute_calibration(bets)["SHIP"]
        self.assertEqual(c["n"], 4)
        self.assertEqual(c["shipped"], 3)
        self.assertEqual(c["rate"], 0.75)               # 3 of 4 shipped
        self.assertEqual(c["score"], round((1 + 1 + 0.5 + 0) / 4, 2))  # 0.62

    def test_shipped_but_unrated_is_half_credit_not_excluded(self):
        # Shipped with no energy rating: it shipped (a known outcome), so it counts --
        # at half credit (did not clearly matter), NOT abstained. Distinct from a bet
        # with no ship/drop outcome at all (which IS excluded; see next test).
        bets = [_bet(i, shipped=True, energy=None) for i in range(N)]
        c = cal.compute_calibration(bets)["SHIP"]
        self.assertEqual(c["n"], N)
        self.assertEqual(c["shipped"], N)
        self.assertEqual(c["score"], 0.5)

    def test_ungraded_outcome_is_excluded_honest_abstention(self):
        # N graded + 1 committed-but-ungraded (no ship/drop). The ungraded one is NOT a
        # miss; it drops out, leaving exactly N in the denominator.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]
        bets.append(_bet(99, shipped=False, dropped=False))  # selected, no outcome yet
        c = cal.compute_calibration(bets)["SHIP"]
        self.assertEqual(c["n"], N)  # the ungraded bet excluded, not counted against

    def test_unselected_bet_excluded(self):
        # Rehearsed to SHIP but never SELECTed (not committed) -> no bet, excluded.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]
        bets.append(_bet(99, shipped=True, energy=HI, selected=False))
        self.assertEqual(cal.compute_calibration(bets)["SHIP"]["n"], N)

    def test_non_graded_verdict_excluded(self):
        # KILL/unknown verdicts never earn a calibration number.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]
        bets += [_bet(50, verdict="KILL", shipped=True, energy=HI),
                 _bet(51, verdict=None, shipped=True, energy=HI)]
        out = cal.compute_calibration(bets)
        self.assertEqual(set(out), {"SHIP"})
        self.assertEqual(out["SHIP"]["n"], N)

    def test_ship_and_revise_bucketed_separately(self):
        bets = ([_bet(i, verdict="SHIP", shipped=True, energy=HI) for i in range(N)]
                + [_bet(100 + i, verdict="REVISE", shipped=False, dropped=True)
                   for i in range(N)])
        out = cal.compute_calibration(bets)
        self.assertEqual(out["SHIP"]["rate"], 1.0)
        self.assertEqual(out["REVISE"]["rate"], 0.0)

    def test_keys_on_judgment_verdict_not_display_verdict(self):
        # If the display verdict diverges from the grading-bound class (a void failure or
        # a terminal re-rehearse), calibration must follow judgment_verdict -- the class
        # whose belief actually moves -- so the number can never contradict the grading.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]  # judgment_verdict=SHIP
        for b in bets:
            b["verdict"] = "REVISE"  # display flipped, but judgment_verdict stays SHIP
        out = cal.compute_calibration(bets)
        self.assertEqual(set(out), {"SHIP"})  # counted under the GRADING class
        self.assertEqual(out["SHIP"]["n"], N)

    def test_bet_without_judgment_verdict_excluded(self):
        # A rehearsed bet whose grounding never recorded (no judgment_verdict) has no
        # graded judgment -> excluded, even though it has a display verdict + outcome.
        bets = [_bet(i, shipped=True, energy=HI) for i in range(N)]
        bets.append({"id": "P-X", "verdict": "SHIP", "selected_at": "2026-06-01 10:00 UTC",
                     "shipped": True, "energy": HI})  # display verdict but no judgment_verdict
        self.assertEqual(cal.compute_calibration(bets)["SHIP"]["n"], N)  # P-X excluded

    def test_tolerates_empty_and_garbage(self):
        self.assertEqual(cal.compute_calibration(None), {})
        self.assertEqual(cal.compute_calibration([]), {})
        self.assertEqual(cal.compute_calibration(["not a dict", 42]), {})


class FormatPhraseTest(unittest.TestCase):
    def test_phrase_for_present_class(self):
        c = {"SHIP": {"n": 7, "shipped": 4, "rate": 0.57, "score": 0.5}}
        self.assertEqual(cal.format_phrase(c, "SHIP"),
                         "My SHIP calls have shipped 4 of 7 so far.")

    def test_empty_for_absent_or_below_floor(self):
        self.assertEqual(cal.format_phrase({}, "SHIP"), "")          # below floor
        self.assertEqual(cal.format_phrase({"SHIP": {}}, "REVISE"), "")  # other class
        self.assertEqual(cal.format_phrase(None, "SHIP"), "")


class SummaryLineSurfaceTest(unittest.TestCase):
    """The felt accountability moment: the rehearse Telegram line appends the verdict
    class's track record -- but only once it clears the n-floor."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.projects = base / "projects.json"
        self.enterContext(mock.patch.object(reh, "PROJECTS_LOG", self.projects))

    JUDGE = "VERDICT: SHIP - solid\n\nWHY IT HOLDS:\nthe risk is real but mitigated\n"

    def test_appends_phrase_above_floor(self):
        argo_store.save_json(
            self.projects, [_bet(i, shipped=True, energy=HI) for i in range(N)])
        out = reh._summary_line("P-200", "SHIP", self.JUDGE)
        self.assertIn(f"My SHIP calls have shipped {N} of {N} so far.", out)

    def test_no_phrase_below_floor(self):
        argo_store.save_json(
            self.projects, [_bet(i, shipped=True, energy=HI) for i in range(N - 1)])
        out = reh._summary_line("P-200", "SHIP", self.JUDGE)
        self.assertNotIn("calls have shipped", out)

    def test_phrase_omitted_when_log_unreadable(self):
        # No project file at all -> load returns [] -> "" phrase, summary still produced.
        out = reh._summary_line("P-200", "SHIP", self.JUDGE)
        self.assertTrue(out.startswith("Rehearsed P-200."))
        self.assertNotIn("calls have shipped", out)


class GatherPerformanceSurfaceTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.projects = base / "projects.json"
        self.enterContext(mock.patch.object(self_mod, "PROJECTS_LOG", self.projects))
        self.enterContext(mock.patch.object(self_mod, "SEEN_PATH", base / "seen.json"))

    def test_calibration_in_performance_snapshot(self):
        argo_store.save_json(
            self.projects, [_bet(i, shipped=True, energy=HI) for i in range(N)])
        perf = self_mod.gather_performance()
        self.assertIn("calibration", perf)
        self.assertEqual(perf["calibration"]["SHIP"]["shipped"], N)

    def test_empty_calibration_when_no_graded_bets(self):
        argo_store.save_json(self.projects, [{"id": "P-001", "energy": 8}])
        self.assertEqual(self_mod.gather_performance()["calibration"], {})


if __name__ == "__main__":
    unittest.main()
