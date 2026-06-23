"""Receipts tests (argo_receipts + the RECEIPTS webhook gate).

F5: surface Argo's graded track record honestly. The whole point is honesty over
coverage -- when there are no scored predictions the summary must SAY "no graded
calls yet", and when a verdict class is below the calibration n-floor it must say
"insufficient data", never a fabricated record. Asserts:
  (a) several scored predictions -> the calls render + (with enough graded bets)
      the calibration number,
  (b) some-but-<n-floor graded bets -> honest insufficient-data line,
  (c) zero graded calls -> "no graded calls yet",
  (d) output is plain text (no markdown, no em dashes),
  (e) the RECEIPTS gate routes deterministically (never reaches the model).

Pure + hermetic: tmp stores, no network, no LLM.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_calibration as cal
import argo_observe as observe
import argo_predictions as pred
import argo_receipts as rec
import argo_store
import argo_webhook as wh


N = cal.CALIBRATION_MIN_N      # the calibration n-floor
HI = pred.MATTERED_ENERGY_MIN  # at/above this = "it mattered" (full credit)


def _scored(pid, claim, correct, voided=False, scored_at="2026-06-15T10:00:01Z"):
    """A scored prediction as score_due/cancel leave it in the store."""
    p = {"id": pid, "belief_id": "WM-001", "claim": claim,
         "metric": {"kind": "project_shipped", "project_id": "P-001"},
         "armed_at": "2026-06-01T10:00:00Z", "due": "2026-06-15T10:00:00Z",
         "scored_at": scored_at, "correct": correct}
    if voided:
        p["voided"] = True
        p["correct"] = None
    return p


def _armed_undue(pid, claim):
    """An armed-but-not-yet-scored prediction (no scored_at) -- not a graded call."""
    return {"id": pid, "belief_id": "WM-001", "claim": claim,
            "metric": {"kind": "project_shipped", "project_id": "P-001"},
            "armed_at": "2026-06-01T10:00:00Z", "due": "2099-01-01T10:00:00Z",
            "scored_at": None, "correct": None}


def _bet(i, verdict="SHIP", energy=HI):
    """A committed, graded SHIP project-log entry calibration counts."""
    return {"id": f"P-{i:03d}", "text": "a bet", "verdict": verdict,
            "judgment_verdict": verdict, "selected_at": "2026-06-01 10:00 UTC",
            "shipped": True, "energy": energy}


class TrackRecordRenderTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.preds = base / "pred.json"
        self.projects = base / "projects.json"
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", self.preds))
        self.enterContext(mock.patch.object(rec, "PROJECTS_LOG", self.projects))

    # (c) zero graded calls -> honest "no graded calls yet"
    def test_zero_graded_calls_is_honest(self):
        out = rec.track_record()
        self.assertIn("No graded calls yet", out)
        # no calibration either -> insufficient data, never a fabricated number
        self.assertIn("insufficient data", out)
        self.assertNotIn("of", out.split("insufficient data")[0].replace(
            "No graded calls yet", ""))  # no "N of M" record fabricated above

    def test_armed_but_undue_and_voided_are_not_graded_calls(self):
        # An armed-undue pred and a voided one are NOT graded calls: the summary must
        # still say "no graded calls yet", never count them.
        argo_store.save_json(self.preds, [
            _armed_undue("EVP-001", "this will hold someday"),
            _scored("EVP-002", "a retracted bet", correct=True, voided=True),
        ])
        out = rec.track_record()
        self.assertIn("No graded calls yet", out)
        self.assertNotIn("retracted bet", out)
        self.assertNotIn("hold someday", out)

    # (a) several scored predictions -> the calls render
    def test_several_graded_calls_render(self):
        argo_store.save_json(self.preds, [
            _scored("EVP-001", "ship latency drops under 200ms", correct=True),
            _scored("EVP-002", "no recurring crash in the parser", correct=False),
            _scored("EVP-003", "the cache cut token spend", correct=True),
        ])
        out = rec.track_record()
        self.assertIn("3 graded calls", out)
        self.assertIn("(2 of 3 held)", out)
        self.assertIn("latency drops under 200ms", out)
        self.assertIn("it held (correct)", out)
        self.assertIn("did not hold (incorrect)", out)

    def test_recent_slice_is_by_scored_at_not_creation_order(self):
        # Stored in creation order, but scored on a DIFFERENT clock: a pred created
        # first can grade last. The recent-N slice must follow scored_at, so the call
        # graded most recently is the one shown, and the header's "most recent" is true.
        argo_store.save_json(self.preds, [
            _scored("EVP-001", "created first graded last",
                    correct=True, scored_at="2026-06-20T00:00:00Z"),
            _scored("EVP-002", "created last graded first",
                    correct=True, scored_at="2026-06-10T00:00:00Z"),
        ] + [_scored(f"EVP-{i:03d}", f"filler {i}", correct=True,
                     scored_at="2026-06-12T00:00:00Z")
             for i in range(3, 3 + rec.RECENT_N - 1)])
        out = rec.track_record()
        # The latest-graded call (EVP-001, 06-20) must be present; the earliest-graded
        # (EVP-002, 06-10) must be the one dropped past the window.
        self.assertIn("created first graded last", out)
        self.assertNotIn("created last graded first", out)

    def test_newline_in_claim_is_flattened(self):
        # An EVOLVE-authored claim can carry newlines / a leading bullet; flatten so a
        # wrapped line can't be eaten by _clean_reply's bullet strip and stays one line.
        argo_store.save_json(self.preds, [
            _scored("EVP-001", "- line one\nline two", correct=True)])
        out = rec.track_record()
        self.assertIn("line one line two ... it held (correct)", out)
        self.assertEqual(wh._clean_reply(out), out)  # stable through the backstop

    def test_recent_n_caps_the_list(self):
        argo_store.save_json(self.preds, [
            _scored(f"EVP-{i:03d}", f"call {i}", correct=True)
            for i in range(rec.RECENT_N + 3)])
        out = rec.track_record()
        # Only the most recent RECENT_N listed; the header reflects that.
        self.assertIn(f"most recent {rec.RECENT_N} graded calls", out)
        self.assertEqual(out.count("it held (correct)"), rec.RECENT_N)
        # newest is last; the oldest beyond the window is dropped
        self.assertNotIn("call 0 ", out)

    # (a, cont.) with enough graded bets the calibration number renders
    def test_calibration_number_renders_above_floor(self):
        argo_store.save_json(self.preds,
                             [_scored("EVP-001", "a call", correct=True)])
        argo_store.save_json(self.projects, [_bet(i) for i in range(N)])
        out = rec.track_record()
        self.assertIn(f"My SHIP calls have shipped {N} of {N} so far.", out)
        self.assertNotIn("insufficient data", out)

    # (b) below the n-floor -> honest insufficient-data line, no number
    def test_below_floor_says_insufficient_data(self):
        argo_store.save_json(self.preds,
                             [_scored("EVP-001", "a call", correct=True)])
        argo_store.save_json(self.projects, [_bet(i) for i in range(N - 1)])
        out = rec.track_record()
        self.assertIn("insufficient data", out)
        self.assertIn(f"n<{N}", out)
        self.assertNotIn("calls have shipped", out)

    def test_corrupt_project_log_tolerated(self):
        argo_store.save_json(self.preds,
                             [_scored("EVP-001", "a call", correct=True)])
        self.projects.write_text("not json{")
        out = rec.track_record()  # must not raise
        self.assertIn("insufficient data", out)

    # (d) plain text: no markdown, no em/en dash
    def test_output_is_plain_text(self):
        argo_store.save_json(self.preds, [
            _scored("EVP-001", "a call", correct=True),
            _scored("EVP-002", "another call", correct=False)])
        argo_store.save_json(self.projects, [_bet(i) for i in range(N)])
        out = rec.track_record()
        for bad in ("**", "##", "—", "–", "__"):
            self.assertNotIn(bad, out, f"plain-text rule broken by {bad!r}")
        # _clean_reply (the webhook backstop) leaves it unchanged.
        self.assertEqual(wh._clean_reply(out), out)


class ReceiptsGateTest(unittest.TestCase):
    """The RECEIPTS webhook command routes deterministically, never to the model."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "p.json"))
        self.enterContext(mock.patch.object(rec, "PROJECTS_LOG", base / "proj.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # A handled RECEIPTS must NEVER reach the model.
        self.model = mock.Mock(side_effect=AssertionError("model path must not run"))
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", self.model))

    def _update(self, text, chat_id=777):
        return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}

    def test_receipts_routes_deterministically_and_is_honest_when_empty(self):
        wh.handle_update(self._update("RECEIPTS"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("No graded calls yet", self.sent[0])

    def test_track_record_alias_routes(self):
        wh.handle_update(self._update("track record"))  # case-insensitive
        self.assertEqual(len(self.sent), 1)
        self.assertIn("No graded calls yet", self.sent[0])


if __name__ == "__main__":
    unittest.main()
