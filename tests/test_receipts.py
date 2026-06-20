"""Receipts tests (argo_receipts): the F5 pull-surface track record.

render_receipts reads the prediction store (scored, non-voided predictions: held vs
missed) and the project-log calibration, and renders a terse plain-text summary Argo can
cite in chat -- or an honest empty state when nothing has graded yet. Pure + hermetic:
tmp stores, no network, no LLM, no real data/*.json. Path constants are overridden so the
renderer reads only the seeded fixtures.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_predictions as pred
import argo_receipts as rec


# Enough committed+graded SHIP bets to clear argo_calibration's n-floor (CALIBRATION_MIN_N
# = 4), so the calibration line actually renders. Three shipped (one high-energy), one
# dropped -> "shipped 3 of 4".
def _seeded_projects():
    base = {"judgment_verdict": "SHIP", "selected_at": "2026-06-01T00:00:00Z"}
    return [
        {"id": "P-001", **base, "shipped": True, "energy": 9},
        {"id": "P-002", **base, "shipped": True, "energy": 4},
        {"id": "P-003", **base, "shipped": True, "energy": 6},
        {"id": "P-004", **base, "dropped": True},
    ]


def _scored(pid, claim, correct, scored_at, voided=False):
    return {
        "id": pid, "belief_id": "WM-001", "claim": claim, "metric": {},
        "days": 14, "created_at": "2026-05-01T00:00:00Z",
        "armed_at": "2026-05-01T00:00:00Z", "due": "2026-05-15T00:00:00Z",
        "scored_at": scored_at, "correct": correct, "voided": voided, "source": "test",
    }


class ReceiptsTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pred_path = self.base / "pred.json"
        self.proj_path = self.base / "proj.json"
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", self.pred_path))
        self.enterContext(mock.patch.object(rec, "PROJECTS_LOG", self.proj_path))

    def _write(self, path, data):
        path.write_text(json.dumps(data))

    def test_renders_predictions_and_calibration(self):
        # Two held, one missed, plus one VOIDED that must not count at all.
        self._write(self.pred_path, [
            _scored("EVP-001", "adopting prompt caching cuts cost", True,
                    "2026-06-01T00:00:00Z"),
            _scored("EVP-002", "the new feed lands within two weeks", False,
                    "2026-06-02T00:00:00Z"),
            _scored("EVP-003", "switching the model improves recall", True,
                    "2026-06-03T00:00:00Z"),
            _scored("EVP-004", "a retracted bet", True, "2026-06-04T00:00:00Z",
                    voided=True),
        ])
        self._write(self.proj_path, _seeded_projects())

        out = rec.render_receipts()

        # Headline counts the three graded, non-voided predictions only.
        self.assertIn("2 held, 1 didn't, out of 3", out)
        # The most-recent graded calls are cited by claim + verdict (voided excluded).
        self.assertIn("switching the model improves recall: held.", out)
        self.assertIn("the new feed lands within two weeks: didn't hold.", out)
        self.assertNotIn("a retracted bet", out)
        # The build-decision calibration line renders (3 of 4 shipped, above n-floor).
        self.assertIn("My SHIP calls have shipped 3 of 4 so far.", out)
        # Argo voice: plain text only.
        self.assertNotIn("*", out)
        self.assertNotIn("—", out)  # no em dash

    def test_empty_state_when_no_track_record(self):
        # No predictions, no committed/graded bets: an honest empty state, no number.
        self._write(self.pred_path, [])
        self._write(self.proj_path, [])

        out = rec.render_receipts()

        self.assertIn("No track record to show yet", out)
        # Never fabricate a count or a calibration when nothing has graded.
        self.assertNotIn("held", out)
        self.assertNotIn("of", out.split("show yet")[0])

    def test_thin_calibration_is_omitted_not_faked(self):
        # One scored prediction but only two committed bets -> below the calibration
        # n-floor, so the calibration line must NOT appear (it is un-surfaceable).
        self._write(self.pred_path, [
            _scored("EVP-001", "a single graded call", True, "2026-06-01T00:00:00Z"),
        ])
        self._write(self.proj_path, _seeded_projects()[:2])

        out = rec.render_receipts()

        self.assertIn("1 held, 0 didn't, out of 1", out)
        self.assertNotIn("SHIP calls have shipped", out)


if __name__ == "__main__":
    unittest.main()
