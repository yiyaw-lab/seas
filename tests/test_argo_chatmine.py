"""Chat-log weakness miner tests (argo_chatmine).

Pure -- no network, no LLM, no real data/*.json: both the chat log
(argo_memory.CHAT_LOG_PATH, read by the miner) and the incident ledger
(argo_incidents.INCIDENTS_PATH, written by it) are patched to a tmp dir.

The pinned done-check runs in BOTH directions:
  (a) a transcript with a clear frustration turn yields >=1 chat_weakness incident;
  (b) NEGATIVE CONTROL -- a clean, satisfied transcript yields ZERO incidents.
The negative control runs first, so a phrase set that over-fires fails loudly
before any positive case can paper over it.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_chatmine
import argo_incidents as inc
import argo_memory
import argo_store


class ChatMineTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.chat_path = base / "chat.json"
        self.inc_path = base / "incidents.json"
        self.wm_path = base / "watermark.json"
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", self.chat_path))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", self.inc_path))
        # The mining watermark must also be tmp-scoped, or the miner would read/write
        # the real data/ store and leak idempotency state between tests.
        self.enterContext(mock.patch.object(argo_chatmine, "WATERMARK_PATH", self.wm_path))

    def _write_chat(self, turns):
        """turns: list of (role, text). Writes the shared chat-log format."""
        argo_store.save_json(self.chat_path, [
            {"ts": "2026-06-20T00:00:00Z", "chat_id": "1", "role": r, "text": t}
            for r, t in turns
        ])

    def _weakness_clusters(self):
        return [c for c in inc.open_clusters(min_count=1)
                if c.get("kind") == "chat_weakness"]

    # --- (b) NEGATIVE CONTROL: a clean transcript records nothing ----------
    # Runs first by name (test_a_*) so an over-firing phrase set fails loudly.

    def test_a_clean_transcript_yields_zero_incidents(self):
        self._write_chat([
            ("Yiya", "hey, can you summarize the latest AI papers?"),
            ("Argo", "sure, here are three..."),
            ("Yiya", "great, thanks. what went wrong with the launch last week?"),
            ("Argo", "the rollout slipped a day..."),
            ("Yiya", "perfect, that's exactly right. is it wrong to ship on a friday?"),
            ("Argo", "depends on your on-call..."),
            ("Yiya", "love it, you nailed it."),
        ])
        recorded = argo_chatmine.mine_chat_log()
        self.assertEqual(recorded, 0)
        self.assertEqual(self._weakness_clusters(), [])

    # --- (a) POSITIVE: a clear frustration turn files an incident ----------

    def test_b_frustration_turn_yields_incident(self):
        self._write_chat([
            ("Yiya", "what's the capital of Australia?"),
            ("Argo", "Sydney."),
            ("Yiya", "no, that's wrong. it's Canberra."),
        ])
        recorded = argo_chatmine.mine_chat_log()
        self.assertGreaterEqual(recorded, 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["kind"], "chat_weakness")

    def test_misunderstood_phrase_fires(self):
        self._write_chat([
            ("Argo", "here's a plan to refactor the whole module."),
            ("Yiya", "you misunderstood, i just wanted a one-line tweak."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        self.assertEqual(len(self._weakness_clusters()), 1)

    def test_not_what_i_meant_fires(self):
        self._write_chat([
            ("Yiya", "that's not what i meant at all."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)

    # --- precision: Argo's OWN turn saying "wrong" must not fire -----------

    def test_argo_turn_is_not_mined(self):
        self._write_chat([
            ("Argo", "you're right, my earlier answer was wrong; that's incorrect."),
            ("Yiya", "ok thanks for fixing it."),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(self._weakness_clusters(), [])

    # --- distinct corrections in different turns roll up by kind ----------

    def test_repeat_same_correction_rolls_into_one_cluster(self):
        self._write_chat([
            ("Yiya", "no that's wrong"),
            ("Argo", "sorry, let me retry..."),
            ("Yiya", "still wrong, try again"),
            ("Argo", "..."),
            ("Yiya", "that's wrong again"),
        ])
        argo_chatmine.mine_chat_log()
        clusters = self._weakness_clusters()
        # 'thats_wrong' fires on turns 1 and 5 (rolls to one cluster); 'still_wrong'
        # and 'wrong_again' are distinct kinds -> distinct clusters. Bounded + > 0.
        self.assertGreaterEqual(len(clusters), 1)
        total = sum(c["count"] for c in clusters)
        self.assertGreaterEqual(total, 2)

    # --- robustness: empty / missing / corrupt log never raises -----------

    def test_missing_log_is_zero_and_no_raise(self):
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)

    def test_corrupt_log_is_zero_and_no_raise(self):
        self.chat_path.write_text("{not valid json")
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)

    def test_only_recent_turns_scanned(self):
        # An old frustration turn pushed out of the scan window is not mined.
        old = [("Yiya", "no, that's wrong")]
        filler = [("Yiya", "ok"), ("Argo", "sure")] * 40
        self._write_chat(old + filler)
        self.assertEqual(argo_chatmine.mine_chat_log(scan_turns=10), 0)

    # --- (a) IDEMPOTENCY (negative control): re-mining the same log files nothing --
    # FAILS before the watermark fix (the 2nd pass re-records every still-matching
    # turn, inflating counts and reopening resolved clusters).

    def test_remine_same_log_records_zero_new(self):
        self._write_chat([
            ("Yiya", "no, that's wrong. it's Canberra."),
            ("Argo", "sorry, you're right."),
            ("Yiya", "you misunderstood the whole request."),
        ])
        first = argo_chatmine.mine_chat_log()
        self.assertGreaterEqual(first, 1)
        before = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        # Second pass over the UNCHANGED log must add no new incidents and must not
        # bump any existing cluster's count.
        second = argo_chatmine.mine_chat_log()
        self.assertEqual(second, 0)
        after = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        self.assertEqual(after, before)

    def test_only_turns_after_watermark_are_mined(self):
        # First run mines the existing turns and advances the watermark; a turn
        # appended afterward is the ONLY thing the next run files.
        self._write_chat([("Yiya", "no, that's wrong")])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        log = argo_store.load_json(self.chat_path, [])
        log.append({"ts": "2026-06-20T01:00:00Z", "chat_id": "1",
                    "role": "Yiya", "text": "you misunderstood me again"})
        argo_store.save_json(self.chat_path, log)
        # Exactly the one new correction-kind is filed (the old 'thats_wrong' turn,
        # already past the watermark, is not re-counted).
        self.assertEqual(argo_chatmine.mine_chat_log(), 1)

    # --- (b) REGEX precision: benign 'stop'/'not to' lines must NOT fire ----------
    # while true stop-corrections still match (the stop_doing_that signal).

    def test_benign_stop_and_not_to_lines_do_not_fire(self):
        self._write_chat([
            ("Yiya", "I told you not to worry, the launch went fine."),
            ("Argo", "glad to hear it."),
            ("Yiya", "I asked you to stop by the store on the way home."),
            ("Argo", "got it."),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(self._weakness_clusters(), [])

    def test_true_stop_correction_still_fires(self):
        self._write_chat([
            ("Yiya", "i already told you to stop doing that."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["kind"], "chat_weakness")


if __name__ == "__main__":
    unittest.main()
