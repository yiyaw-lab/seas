"""Acted-on-push store regression tests (argo_pushes).

Locks the measurement F1 needs: a push is recorded, a user reply WITHIN the
window links the most-recent open push (and marks it linked), act_on_rate reflects
the linked fraction, and a reply OUTSIDE the window links nothing. Pure -- no
network/LLM/real data: PUSHES_PATH is patched to a tmp dir.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_pushes


class PushStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = tmp / "argo_pushes.json"
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", self.path))

    def _events(self):
        import argo_store
        return argo_store.load_json(self.path, [])

    def test_record_link_rate_and_window(self):
        base = 1_000_000.0

        # Record a push, then a reply within the window links it.
        pid = argo_pushes.record("project", "first push", ts=base)
        self.assertEqual(pid, 1)

        linked_id = argo_pushes.link_reply("chat-1", ts=base + 60)
        self.assertEqual(linked_id, pid)

        evt = next(e for e in self._events() if e["id"] == pid)
        self.assertTrue(evt["linked"])
        self.assertEqual(evt["linked_ts"], base + 60)

        # A 2nd push stays unlinked -> act_on_rate is 1 of 2 = 0.5.
        argo_pushes.record("watch", "second push", ts=base + 120)
        self.assertEqual(argo_pushes.act_on_rate(), 0.5)

        # A reply OUTSIDE the window (past LINK_WINDOW_SECONDS after the 2nd push)
        # links nothing, and the rate is unchanged.
        far = base + 120 + argo_pushes.LINK_WINDOW_SECONDS + 1
        self.assertIsNone(argo_pushes.link_reply("chat-1", ts=far))
        self.assertEqual(argo_pushes.act_on_rate(), 0.5)

    def test_act_on_rate_zero_when_empty(self):
        self.assertEqual(argo_pushes.act_on_rate(), 0.0)

    def test_record_idempotent_within_window(self):
        """A retry that re-POSTs identical (kind, content) within the dedup window
        must collapse to ONE row with the SAME id (the at-least-once double-record
        the round-2 retry could otherwise cause); identical content OUTSIDE the
        window records separately (a genuinely distinct later send).
        """
        base = 2_000_000.0

        # First record.
        first = argo_pushes.record("project", "same body", ts=base)
        # The retry re-POSTs the SAME (kind, content) ~10s later (within window):
        # one row, same id, no new append.
        retry = argo_pushes.record("project", "same body", ts=base + 10)
        self.assertEqual(retry, first)
        self.assertEqual(len(self._events()), 1)

        # The dedup must not mutate the existing row's linked state.
        self.assertFalse(self._events()[0]["linked"])

        # Identical content OUTSIDE the window is a distinct push -> a SECOND row.
        far_ts = base + argo_pushes.RECORD_DEDUP_SECONDS + 1
        second = argo_pushes.record("project", "same body", ts=far_ts)
        self.assertNotEqual(second, first)
        self.assertEqual(len(self._events()), 2)

        # A different kind with the same content within the window is also distinct
        # (content_hash does not incorporate kind, so the kind guard must catch it).
        other = argo_pushes.record("watch", "same body", ts=base + 10)
        self.assertNotEqual(other, first)
        self.assertEqual(len(self._events()), 3)


class _Resp:
    """Minimal context-manager stand-in for a urlopen 2xx response."""
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


class PostToWebhookRetryTest(unittest.TestCase):
    """post_to_webhook does ONE fast bounded retry on a TRANSIENT failure only
    (Bugbot PR #36 finding 2, round 2): a flaky/restarting webhook gets a second
    chance to record the push so a later reply doesn't mislink a different open
    push, but a permanent 4xx is not retried and delivery is never delayed beyond
    one extra short attempt. Pure -- urlopen is stubbed, no network.
    """

    def setUp(self):
        self.enterContext(mock.patch.dict(
            "os.environ",
            {"WEBHOOK_URL": "https://x.example", "ARGO_MCP_TOKEN": "t"}))

    def test_transient_then_success_retries_and_records(self):
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError("timed out")  # transient
            return _Resp(200)

        with mock.patch.object(argo_pushes.urllib.request, "urlopen", fake_urlopen):
            result = argo_pushes.post_to_webhook("project", "hi")
        self.assertTrue(result.recorded)
        self.assertFalse(result.suppressed)
        self.assertEqual(len(calls), 2)  # retried exactly once

    def test_persistent_5xx_retries_once_then_gives_up(self):
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            raise urllib.error.HTTPError("https://x", 503, "unavailable", {}, None)

        with mock.patch.object(argo_pushes.urllib.request, "urlopen", fake_urlopen):
            result = argo_pushes.post_to_webhook("project", "hi")
        self.assertFalse(result.recorded)
        self.assertFalse(result.suppressed)  # a failed POST is fail-open, not suppressed
        self.assertEqual(len(calls), 2)  # 5xx is transient -> one retry, then stop

    def test_permanent_4xx_not_retried(self):
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            raise urllib.error.HTTPError("https://x", 401, "unauthorized", {}, None)

        with mock.patch.object(argo_pushes.urllib.request, "urlopen", fake_urlopen):
            result = argo_pushes.post_to_webhook("project", "hi")
        self.assertFalse(result.recorded)
        self.assertFalse(result.suppressed)  # a failed POST is fail-open, not suppressed
        self.assertEqual(len(calls), 1)  # 4xx is permanent -> no retry


if __name__ == "__main__":
    unittest.main()
