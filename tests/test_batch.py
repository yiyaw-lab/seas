"""Tests for the Anthropic Message Batches primitive (argo_batch, EV-003).

Locks the three load-bearing guarantees of the bulk-scoring primitive:
  (a) N (custom_id, prompt) pairs assemble into ONE batch with UNIQUE custom_ids
      (and a collision is rejected, not silently coalesced);
  (b) the poll loop transitions pending -> ended (bounded) and maps results back
      BY custom_id, NOT by position (results are returned out of order on purpose);
  (c) a failed/errored item is SURFACED (ok=False + error), never dropped.

Pure: no network, no LLM, no real data files. The anthropic SDK's batch types
are faked in sys.modules so build_requests runs without the dep, and a fake
client + a no-op sleep drive run_batch with zero waits.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock


def _install_fake_anthropic_types():
    """Stub the anthropic.types.* request constructors build_requests imports.

    They're just thin record holders here — we capture model/params so the test
    can assert on the assembled batch without the real SDK installed.
    """
    create_params_mod = types.ModuleType("anthropic.types.message_create_params")
    create_params_mod.MessageCreateParamsNonStreaming = lambda **kw: dict(kw)

    batch_params_mod = types.ModuleType(
        "anthropic.types.messages.batch_create_params")
    batch_params_mod.Request = lambda *, custom_id, params: SimpleNamespace(
        custom_id=custom_id, params=params)

    return {
        "anthropic": types.ModuleType("anthropic"),
        "anthropic.types": types.ModuleType("anthropic.types"),
        "anthropic.types.message_create_params": create_params_mod,
        "anthropic.types.messages": types.ModuleType("anthropic.types.messages"),
        "anthropic.types.messages.batch_create_params": batch_params_mod,
    }


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _succeeded(custom_id, text):
    msg = SimpleNamespace(content=[_text_block(text)])
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=msg),
    )


def _errored(custom_id, error_type):
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(
            type="errored", error=SimpleNamespace(type=error_type)),
    )


class FakeBatchesClient:
    """A stand-in for client.messages.batches with a scripted status sequence.

    create() records the requests and returns a pending batch; retrieve() walks
    the scripted statuses (so the poll loop must transition pending -> ended);
    results() returns the canned results in a deliberately SHUFFLED order so the
    caller cannot get away with mapping by position.
    """

    def __init__(self, statuses, results):
        self._statuses = list(statuses)
        self._results = list(results)
        self.created_requests = None
        self.retrieve_calls = 0

        batches = SimpleNamespace(
            create=self._create, retrieve=self._retrieve, results=self._results_fn)
        self.messages = SimpleNamespace(batches=batches)

    def _create(self, requests):
        self.created_requests = requests
        return SimpleNamespace(id="batch_test", processing_status=self._statuses[0])

    def _retrieve(self, batch_id):
        self.retrieve_calls += 1
        idx = min(self.retrieve_calls, len(self._statuses) - 1)
        return SimpleNamespace(id=batch_id, processing_status=self._statuses[idx])

    def _results_fn(self, batch_id):
        return iter(self._results)


class BuildRequestsTest(unittest.TestCase):
    def setUp(self):
        self._mod_patch = mock.patch.dict(
            sys.modules, _install_fake_anthropic_types())
        self._mod_patch.start()
        import argo_batch
        self.batch = argo_batch

    def tearDown(self):
        self._mod_patch.stop()

    def test_n_items_one_batch_unique_ids(self):
        items = [(f"sig-{i}", f"prompt {i}") for i in range(4)]
        requests = self.batch.build_requests(items, "claude-sonnet-4-6")
        self.assertEqual(len(requests), 4)
        ids = [r.custom_id for r in requests]
        self.assertEqual(ids, ["sig-0", "sig-1", "sig-2", "sig-3"])
        self.assertEqual(len(set(ids)), 4)  # all unique

    def test_duplicate_custom_id_rejected(self):
        items = [("dup", "a"), ("dup", "b")]
        with self.assertRaises(ValueError):
            self.batch.build_requests(items, "claude-sonnet-4-6")

    def test_opus_omits_temperature_sonnet_keeps_it(self):
        # Reuses argo_observe._rejects_temperature: opus-4-8 must get no temperature.
        (opus,) = self.batch.build_requests([("x", "p")], "claude-opus-4-8")
        self.assertNotIn("temperature", opus.params)
        (sonnet,) = self.batch.build_requests([("x", "p")], "claude-sonnet-4-6")
        self.assertEqual(sonnet.params["temperature"], 1.0)


class RunBatchTest(unittest.TestCase):
    def setUp(self):
        self._mod_patch = mock.patch.dict(
            sys.modules, _install_fake_anthropic_types())
        self._mod_patch.start()
        import argo_batch
        self.batch = argo_batch

    def tearDown(self):
        self._mod_patch.stop()

    def test_poll_then_map_results_by_custom_id(self):
        # pending on create, pending on first retrieve, ended on the second.
        statuses = ["in_progress", "in_progress", "ended"]
        # Results returned OUT OF ORDER relative to the request list, to prove
        # the mapping is by custom_id, not by position.
        results = [_succeeded("sig-2", "two"), _succeeded("sig-0", "zero"),
                   _succeeded("sig-1", "one")]
        client = FakeBatchesClient(statuses, results)
        sleeps = []

        items = [("sig-0", "p0"), ("sig-1", "p1"), ("sig-2", "p2")]
        out = self.batch.run_batch(
            items, "claude-sonnet-4-6", client=client,
            sleep=lambda s: sleeps.append(s), poll_interval_s=1, max_wait_s=100)

        # One batch was created with all three requests.
        self.assertEqual(len(client.created_requests), 3)
        # The loop actually polled (pending -> ended), not short-circuited.
        self.assertGreaterEqual(client.retrieve_calls, 1)
        self.assertGreater(len(sleeps), 0)
        # Mapped back by custom_id despite the shuffled result order.
        self.assertEqual(out["sig-0"].text, "zero")
        self.assertEqual(out["sig-1"].text, "one")
        self.assertEqual(out["sig-2"].text, "two")
        self.assertTrue(all(r.ok for r in out.values()))

    def test_partial_failure_is_surfaced_not_dropped(self):
        statuses = ["ended"]  # already ended on create (tiny batch)
        results = [_succeeded("ok-1", "good"), _errored("bad-1", "invalid_request")]
        client = FakeBatchesClient(statuses, results)

        items = [("ok-1", "p"), ("bad-1", "p")]
        out = self.batch.run_batch(
            items, "claude-sonnet-4-6", client=client, sleep=lambda s: None)

        # The failed item is present (not dropped) and marked not-ok with its error.
        self.assertIn("bad-1", out)
        self.assertFalse(out["bad-1"].ok)
        self.assertEqual(out["bad-1"].status, "errored")
        self.assertIn("invalid_request", out["bad-1"].error)
        # The good item still succeeded alongside it.
        self.assertTrue(out["ok-1"].ok)
        self.assertEqual(out["ok-1"].text, "good")

    def test_unbounded_wait_raises_timeout(self):
        # Never reaches "ended" -> the bounded loop must raise, not spin forever.
        client = FakeBatchesClient(["in_progress", "in_progress"], [])
        with self.assertRaises(TimeoutError):
            self.batch.run_batch(
                [("x", "p")], "claude-sonnet-4-6", client=client,
                sleep=lambda s: None, poll_interval_s=1, max_wait_s=-1)

    def test_empty_items_returns_empty(self):
        out = self.batch.run_batch([], "claude-sonnet-4-6", client=object())
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
