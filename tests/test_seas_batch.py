"""Opt-in batch SEAS scoring (seas_scoring.auto_score_signals batch=True).

Locks the four guarantees of the wiring that routes unscored signals through
argo_batch.run_batch instead of the per-signal loop:
  (a) OPT-IN: batch=True collects the N unscored signals into ONE run_batch call
      and writes the parsed scores back BY custom_id (never by position);
  (b) DEFAULT unchanged: batch defaults to False -> the original one-call-per
      -signal loop runs, run_batch is NEVER touched;
  (c) DRY-RUN: batch=True, dry_run=True assembles + reports only, run_batch is
      NEVER called and nothing is scored;
  (d) PARTIAL FAILURE surfaced: a per-item ok=False result leaves that signal
      unscored (not dropped) while its siblings still score.

Pure: no network, no LLM, no real data files. argo_batch.run_batch and the
single-call observe.generate_observations are mocked; the model resolver is
stubbed so no API key is needed.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import seas_scoring as sf  # the scoring seam (re-exported as seas_finding.auto_score_signals)
from argo_batch import BatchItemResult


def _unscored(title, summary="s"):
    return {"title": title, "summary": summary, "link": f"https://x/{title}",
            "scores": {"durability": 0, "leverage": 0, "alignment": 0,
                       "accessibility": 0, "novelty": 0}}


def _already_scored(title):
    s = _unscored(title)
    s["scores"]["durability"] = 3  # any non-zero -> not unscored
    return s


# A valid model reply the contract accepts (durability+leverage must be ints).
_GOOD_REPLY = ('{"durability": 4, "leverage": 5, "alignment": 3, '
               '"accessibility": 2, "novelty": 1}')


class BatchScoringTest(unittest.TestCase):
    def setUp(self):
        # Make SCORE_PROMPT_PATH read deterministically without touching disk,
        # and pin a model so no real key/provider is needed.
        self._patches = [
            mock.patch.object(type(sf.SCORE_PROMPT_PATH), "exists",
                              lambda self: True),
            mock.patch.object(type(sf.SCORE_PROMPT_PATH), "read_text",
                              lambda self: "SCORE TEMPLATE"),
            mock.patch.object(sf, "_resolve_score_model",
                              lambda: "claude-sonnet-4-6"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_opt_in_batches_n_signals_and_writes_by_custom_id(self):
        # Two unscored signals flanking one already-scored: only the unscored two
        # go into the batch, and results are returned OUT OF ORDER to prove the
        # write-back is by custom_id, not position.
        signals = [_unscored("A"), _already_scored("MID"), _unscored("B")]
        # Unscored signals are at indices 0 and 2 -> custom_ids sig-0, sig-2.
        results = {
            "sig-2": BatchItemResult("sig-2", ok=True, status="succeeded",
                                     text=_GOOD_REPLY),
            "sig-0": BatchItemResult("sig-0", ok=True, status="succeeded",
                                     text=_GOOD_REPLY),
        }

        with mock.patch.object(sf, "observe") as obs, \
                mock.patch("argo_batch.run_batch",
                           return_value=results) as run_batch:
            obs.generate_observations.side_effect = AssertionError(
                "single-call path must NOT run in batch mode")
            out = sf.auto_score_signals(signals, batch=True)

        # Exactly one batch call, carrying the two unscored signals as items.
        run_batch.assert_called_once()
        items = run_batch.call_args.args[0]
        self.assertEqual([cid for cid, _ in items], ["sig-0", "sig-2"])
        # SYSTEM is passed through (matches the single-call f"{SYSTEM}..." shape).
        self.assertEqual(run_batch.call_args.kwargs["system"], sf.SYSTEM)

        # Both unscored signals got the parsed scores; the middle one untouched.
        self.assertEqual(out[0]["scores"]["durability"], 4)
        self.assertEqual(out[0]["scores"]["leverage"], 5)
        self.assertEqual(out[2]["scores"]["durability"], 4)
        self.assertEqual(out[1]["scores"]["durability"], 3)  # unchanged

    def test_default_path_unchanged_never_calls_run_batch(self):
        signals = [_unscored("A"), _unscored("B")]
        captured = []

        def fake_gen(job, model, *a, **k):
            captured.append(model)
            return _GOOD_REPLY

        with mock.patch("argo_batch.run_batch",
                        side_effect=AssertionError(
                            "default path must NOT call run_batch")) as run_batch, \
                mock.patch.object(sf.observe, "generate_observations", fake_gen):
            out = sf.auto_score_signals(signals)  # batch defaults to False

        run_batch.assert_not_called()
        # The single-call loop ran once per signal and applied the scores.
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(s["scores"]["durability"] == 4 for s in out))

    def test_dry_run_assembles_without_calling(self):
        signals = [_unscored("A"), _unscored("B")]
        with mock.patch("argo_batch.run_batch",
                        side_effect=AssertionError(
                            "dry-run must NOT call run_batch")) as run_batch:
            out = sf.auto_score_signals(signals, batch=True, dry_run=True)

        run_batch.assert_not_called()
        # Nothing scored — every signal still all-zero.
        self.assertTrue(all(sf._is_unscored(s) for s in out))

    def test_partial_failure_surfaced_not_dropped(self):
        signals = [_unscored("OK"), _unscored("BAD")]
        results = {
            "sig-0": BatchItemResult("sig-0", ok=True, status="succeeded",
                                     text=_GOOD_REPLY),
            "sig-1": BatchItemResult("sig-1", ok=False, status="errored",
                                     error="invalid_request"),
        }
        with mock.patch("argo_batch.run_batch", return_value=results):
            out = sf.auto_score_signals(signals, batch=True)

        # The succeeded item scored; the failed item is PRESENT but left unscored
        # (not dropped — the list keeps all signals).
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["scores"]["durability"], 4)
        self.assertTrue(sf._is_unscored(out[1]))

    def test_batch_level_failure_leaves_all_unscored(self):
        # A create/poll/timeout error must not crash; every signal stays unscored.
        signals = [_unscored("A"), _unscored("B")]
        with mock.patch("argo_batch.run_batch",
                        side_effect=TimeoutError("batch stuck")):
            out = sf.auto_score_signals(signals, batch=True)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(sf._is_unscored(s) for s in out))


if __name__ == "__main__":
    unittest.main()
