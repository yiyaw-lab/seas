"""Acted-on-push linkage fires on DETERMINISTIC reply paths (argo_webhook).

The bug this locks (PR #36 finding 1): link_reply() used to live inside
_generate_reply, so only the LLM-handled replies linked. The COMMON replies to a
proactive push are deterministic -- a bare 1-10 rating, SELECT, REHEARSE -- and
they return upstream of _generate_reply, so the push never got linked and
act_on_rate undercounted. The fix moves link_reply to a single chokepoint at the
top of handle_update, before the deterministic-vs-LLM fork, so EVERY user reply
links exactly once.

Pure + hermetic: PUSHES_PATH -> tmp dir, send_telegram + _record_rating stubbed,
no network/LLM/real data.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_pushes
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class DeterministicRatingLinksPushTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = tmp / "argo_pushes.json"
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", self.path))

        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # The rating path's project bookkeeping is irrelevant here -- stub it so the
        # test is pure and stays focused on linkage, not the rating store.
        self.enterContext(mock.patch.object(
            wh, "_record_rating", lambda *a, **k: "Got 7/10, logged it."))
        # A deterministic rating must never reach the model.
        self.enterContext(mock.patch.object(
            wh, "_generate_reply",
            mock.Mock(side_effect=AssertionError("model path must not run"))))

    def _rows(self):
        import argo_store
        return argo_store.load_json(self.path, [])

    def test_bare_rating_reply_links_the_open_push(self):
        # A push exists (the weekly project Argo sent), still unlinked.
        pid = argo_pushes.record("project", "the weekly project")
        self.assertFalse(self._rows()[0]["linked"])

        # A bare 1-10 rating is handled deterministically (returns before
        # _generate_reply) -- yet it must still link the open push.
        wh.handle_update(_update("7"))

        self.assertEqual(self.sent, ["Got 7/10, logged it."])
        row = next(r for r in self._rows() if r["id"] == pid)
        self.assertTrue(row["linked"])
        self.assertIsNotNone(row["linked_ts"])
        # act_on_rate now reflects the engaged push (was 0.0 before the link).
        self.assertEqual(argo_pushes.act_on_rate(), 1.0)

    def test_rating_links_exactly_once_no_double_link(self):
        # Two open pushes within the link window; a single rating reply links only
        # the most-recent one (no double-link from a stray second call site).
        import time
        now = time.time()
        argo_pushes.record("project", "older push", ts=now - 120)
        newer = argo_pushes.record("watch", "newer push", ts=now - 60)
        wh.handle_update(_update("9"))
        rows = self._rows()
        linked = [r for r in rows if r["linked"]]
        self.assertEqual([r["id"] for r in linked], [newer])


if __name__ == "__main__":
    unittest.main()
