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


if __name__ == "__main__":
    unittest.main()
