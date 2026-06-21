"""Steerable-proactiveness gate tests (F6, PRD: rare / right / steerable).

Locks the gate the PRD demands now that F1 makes proactiveness measurable: before
an unprompted push is sent, score it stakes*confidence and only send when it
clears a threshold that the user TUNES and that AUTO-DIALS-UP when the recent
act-on-rate is low. The measurement-trap guard: at COLD START (too few recorded
pushes) the bar must NOT auto-raise, so the first pushes -- the ones that build
the act-on-rate -- aren't strangled, and no divide-by-zero can arise.

Cases (each fails before the F6 gate exists, passes after):
  (a) a low-stakes push is SUPPRESSED once the act-on-rate is low (with history);
  (b) a high-stakes/high-confidence push PASSES even when the rate is low;
  (c) the threshold is TUNABLE via set_threshold/get_threshold and the round-trip
      through the gate respects it;
  (d) COLD START: no dial-up below MIN_PUSHES_FOR_DIALUP, and act_on_rate on an
      empty store is 0.0 (no divide-by-zero), so an in-bar push still sends;
  (e) the /push handler bridges the verdict: a suppressed push is NOT recorded and
      the response says suppressed=True; an allowed one records and says False.

Pure -- no network/LLM/real data: PUSHES_PATH + PROACTIVE_PATH patched to a tmp
dir. Run from the repo root: PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_pushes
import argo_store


class _PushTmp(unittest.TestCase):
    """Both stores on a fresh tmp dir; helpers to seed the push history."""

    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pushes = tmp / "argo_pushes.json"
        self.proactive = tmp / "argo_proactive.json"
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", self.pushes))
        self.enterContext(mock.patch.object(argo_pushes, "PROACTIVE_PATH", self.proactive))

    def _seed_history(self, total, linked):
        """Write `total` push rows, `linked` of them acted-on, so act_on_rate is a
        known low/high value and the cold-start floor is cleared."""
        rows = [{"id": i + 1, "ts": 1_000_000 + i, "kind": "watch",
                 "content_hash": f"h{i}", "linked": i < linked, "linked_ts": None}
                for i in range(total)]
        argo_store.save_json(self.pushes, rows)


class GateThresholdTest(_PushTmp):
    def test_low_stakes_push_suppressed_when_act_on_rate_low(self):
        # (a) A long history with almost no engagement -> low act-on-rate -> the bar
        # auto-dials-up. A low-stakes push (0.3 * 0.4 = 0.12) is below it: SUPPRESS.
        self._seed_history(total=10, linked=0)  # rate 0.0
        self.assertLess(argo_pushes.act_on_rate(), 0.1)
        allowed, _ = argo_pushes.should_send("nudge", stakes=0.3, confidence=0.4)
        self.assertFalse(allowed)

    def test_high_stakes_push_passes_even_when_rate_low(self):
        # (b) Same low-rate history, but a genuinely high-stakes + high-confidence
        # push (0.9 * 0.95 = 0.855) clears even the fully dialed-up bar (base 0.30 +
        # MAX_DIAL_UP 0.40 = 0.70). The right thing still gets through.
        self._seed_history(total=10, linked=0)
        allowed, _ = argo_pushes.should_send("alert", stakes=0.9, confidence=0.95)
        self.assertTrue(allowed)

    def test_threshold_is_tunable(self):
        # (c) Raise the base bar high enough that a mid push is suppressed; lower it
        # and the SAME push passes. Cold start (no history) so no dial-up confounds.
        self.assertEqual(argo_pushes.set_threshold(0.8), 0.8)
        self.assertEqual(argo_pushes.get_threshold(), 0.8)
        allowed_high, _ = argo_pushes.should_send("x", stakes=0.6, confidence=0.6)  # 0.36
        self.assertFalse(allowed_high)

        argo_pushes.set_threshold(0.1)
        allowed_low, _ = argo_pushes.should_send("x", stakes=0.6, confidence=0.6)
        self.assertTrue(allowed_low)

    def test_set_threshold_clamps_and_rejects_garbage(self):
        self.assertEqual(argo_pushes.set_threshold(1.7), 1.0)   # clamp high
        self.assertEqual(argo_pushes.set_threshold(-0.5), 0.0)  # clamp low
        self.assertEqual(argo_pushes.set_threshold("0.42"), 0.42)  # numeric string ok
        with self.assertRaises(ValueError):
            argo_pushes.set_threshold("loud")

    def test_get_threshold_default_on_missing_or_corrupt(self):
        # Nothing written yet -> the default, not an error.
        self.assertEqual(argo_pushes.get_threshold(), argo_pushes.DEFAULT_THRESHOLD)
        # A corrupt/wrong-shape store also falls back, never raises.
        argo_store.save_json(self.proactive, {"threshold": "nope"})
        self.assertEqual(argo_pushes.get_threshold(), argo_pushes.DEFAULT_THRESHOLD)


class GateFailOpenTest(_PushTmp):
    def test_internal_error_fails_open_allows_send(self):
        # A gate-internal error (here a volume read error reaching the threshold
        # read) must NEVER silence a send: should_send fails OPEN (allowed=True).
        with mock.patch.object(argo_pushes, "effective_threshold",
                               side_effect=OSError("volume read error")):
            allowed, reason = argo_pushes.should_send("project")
        self.assertTrue(allowed)
        self.assertIn("fail-open", reason)


class ColdStartTest(_PushTmp):
    def test_no_dialup_below_min_pushes(self):
        # (d) Below MIN_PUSHES_FOR_DIALUP, even a 0.0 act-on-rate must NOT raise the
        # bar -- the effective threshold equals the base. The early pushes that BUILD
        # the rate aren't strangled by a rate computed from too little data.
        self._seed_history(total=argo_pushes.MIN_PUSHES_FOR_DIALUP - 1, linked=0)
        self.assertEqual(argo_pushes.effective_threshold(), argo_pushes.get_threshold())
        # ...and once enough history accrues at a low rate, it DOES dial up.
        self._seed_history(total=argo_pushes.MIN_PUSHES_FOR_DIALUP, linked=0)
        self.assertGreater(argo_pushes.effective_threshold(), argo_pushes.get_threshold())

    def test_empty_store_no_divide_by_zero_and_in_bar_push_sends(self):
        # (d) Empty store: act_on_rate is 0.0 (not a crash), effective == base, and a
        # default-kind push at cold start still sends -- we never suppress everything
        # for lack of a history.
        self.assertEqual(argo_pushes.act_on_rate(), 0.0)
        self.assertEqual(argo_pushes.effective_threshold(), argo_pushes.DEFAULT_THRESHOLD)
        allowed, _ = argo_pushes.should_send("project")  # default 0.7*0.8 = 0.56
        self.assertTrue(allowed)


class PushEndpointGateTest(_PushTmp):
    """(e) The /push handler runs the gate before recording and bridges the verdict.

    A suppressed push is NOT recorded (it was never sent, so it must not enter
    act_on_rate's denominator); an allowed one records as before.
    """

    def setUp(self):
        super().setUp()
        import argo_webhook
        self.enterContext(mock.patch.object(argo_webhook, "ARGO_MCP_TOKEN", "tok-123"))
        self.client = argo_webhook.create_app().test_client()

    def _rows(self):
        return argo_store.load_json(self.pushes, [])

    def _post(self, body):
        return self.client.post("/push", json=body,
                                headers={"Authorization": "Bearer tok-123"})

    def test_suppressed_push_not_recorded(self):
        # Bar raised above a low-stakes push: the handler reports suppressed and
        # writes NO row, so the denominator can't count a push that never sent.
        argo_pushes.set_threshold(0.9)
        r = self._post({"kind": "nudge", "content": "tiny", "stakes": 0.2, "confidence": 0.3})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["suppressed"])
        self.assertIsNone(body["id"])
        self.assertEqual(self._rows(), [])

    def test_allowed_push_records(self):
        argo_pushes.set_threshold(0.1)
        r = self._post({"kind": "project", "content": "real one",
                        "stakes": 0.9, "confidence": 0.9})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body["suppressed"])
        self.assertEqual(body["id"], 1)
        self.assertEqual(len(self._rows()), 1)


if __name__ == "__main__":
    unittest.main()
