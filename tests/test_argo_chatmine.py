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

    def test_scan_bounded_to_scan_turns_per_run(self):
        # One run scans at most `scan_turns` turns (here: the oldest 10), so a
        # frustration turn BEYOND that window is deferred, not mined this run.
        old = [("Yiya", "no, that's wrong")]
        filler = [("Yiya", "ok"), ("Argo", "sure")] * 40
        self._write_chat(old + filler)
        # The oldest 10 turns hold the single frustration line (index 0): one run
        # mines exactly that and stops -- it does NOT reach deep into the backlog.
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(scan_turns=10), 1)
        # The watermark advanced by only the scanned amount, never the full length.
        self.assertEqual(argo_store.load_json(self.wm_path, {})["mined_turns"], 10)

    def test_backlog_larger_than_scan_turns_is_fully_caught_up_oldest_first(self):
        # The round-3 fix: when more than `scan_turns` turns arrive since the
        # watermark, the OLDEST-unmined slice is scanned first and the watermark
        # advances by only what was scanned -- so a weakness turn buried in the
        # middle of a big backlog is eventually mined, never permanently skipped.
        scan = 5
        # Build a backlog (> scan_turns) with a weakness turn at the START, MIDDLE
        # and END so a "newest slice only" miner would miss the first two forever.
        turns = []
        weakness_positions = (0, 12, 23)
        for i in range(24):
            if i in weakness_positions:
                turns.append(("Yiya", "no, that's wrong"))
            else:
                turns.append(("Yiya" if i % 2 == 0 else "Argo", "ok"))
        self._write_chat(turns)
        total = len(turns)  # 24
        # Drive successive daily runs until the watermark reaches the log end.
        runs = 0
        while argo_store.load_json(self.wm_path, {}).get("mined_turns", 0) < total:
            before = argo_store.load_json(self.wm_path, {}).get("mined_turns", 0)
            argo_chatmine.mine_chat_log(scan_turns=scan)
            after = argo_store.load_json(self.wm_path, {})["mined_turns"]
            # Per-run work stays bounded: the watermark advances by at most scan_turns.
            self.assertLessEqual(after - before, scan)
            self.assertGreater(after, before)  # always makes forward progress
            runs += 1
            self.assertLess(runs, 100)  # guard against a non-advancing loop
        # Every weakness turn across the WHOLE backlog eventually filed its incident:
        # 'no, that's wrong' is one signal-kind -> one rolled-up cluster, count == 3.
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], len(weakness_positions))
        # And nothing is ever re-mined: a final run over the fully-caught-up log
        # records zero and does not bump the cluster.
        self.assertEqual(argo_chatmine.mine_chat_log(scan_turns=scan), 0)
        self.assertEqual(self._weakness_clusters()[0]["count"], len(weakness_positions))

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

    # --- (a) EMPTY/UNREADABLE LOG preserves the watermark (does not reset to 0) ----
    # FAILS before the fix: an empty read advanced the stored watermark to 0, so the
    # next good read re-mined every already-mined turn (re-bumping clusters,
    # reopening resolved incidents).

    def test_empty_log_preserves_watermark_and_no_remine(self):
        self._write_chat([
            ("Yiya", "no, that's wrong. it's Canberra."),
            ("Argo", "sorry, you're right."),
            ("Yiya", "you misunderstood the whole request."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        wm = argo_store.load_json(self.wm_path, {})["mined_turns"]
        self.assertEqual(wm, 3)
        before = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        # A later run finds the log empty/unreadable -- the watermark must survive.
        self.chat_path.write_text("{not valid json")
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(argo_store.load_json(self.wm_path, {})["mined_turns"], 3)
        # The next real run over the unchanged log re-reads it and files NOTHING new.
        self._write_chat([
            ("Yiya", "no, that's wrong. it's Canberra."),
            ("Argo", "sorry, you're right."),
            ("Yiya", "you misunderstood the whole request."),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        after = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        self.assertEqual(after, before)

    # --- (b) SHRUNK/REBUILT LOG re-mines from 0 (its fresh turns are not skipped) --
    # FAILS before the fix: a watermark past the new (shorter) log length clamped to
    # the length, scanned nothing, then advanced -- treating the rebuilt log's new
    # turns as already-mined.

    def test_shrunk_log_remines_fresh_turns(self):
        # A high stored watermark from a previously longer log.
        argo_store.save_json(self.wm_path, {"mined_turns": 50})
        # The log is rebuilt SHORTER with fresh weakness turns at new positions.
        self._write_chat([
            ("Yiya", "no, that's wrong. it's Canberra."),
            ("Argo", "sorry."),
            ("Yiya", "you misunderstood the whole request."),
        ])
        recorded = argo_chatmine.mine_chat_log()
        self.assertGreaterEqual(recorded, 1)
        self.assertGreaterEqual(len(self._weakness_clusters()), 1)
        # The watermark is re-anchored to the rebuilt log's length.
        self.assertEqual(argo_store.load_json(self.wm_path, {})["mined_turns"], 3)

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

    # --- (FINDING 1) same weakness LABEL, DIFFERENT wording -> ONE cluster ---------
    # The round-4 fix: key a chat_weakness incident on the signal LABEL, not the full
    # matched substring. argo_incidents._fingerprint normalizes digits/URLs/hex but NOT
    # varied wording, so before the fix three same-category corrections phrased three
    # different ways formed three count-1 clusters and diagnose()'s min_count=3 gate
    # never tripped -- defeating the miner's purpose. After the fix they roll up to one
    # cluster whose count reaches min_count.

    def test_same_label_varied_wording_rolls_to_one_cluster_reaching_min_count(self):
        # Three turns, all the 'misunderstood' label, each worded differently so the
        # matched substring (you misunderstood / you completely misread / you missed
        # the point) differs every time.
        self._write_chat([
            ("Yiya", "you misunderstood me, that's not the ask."),
            ("Argo", "sorry, let me redo it."),
            ("Yiya", "you completely misread my request again."),
            ("Argo", "ok, retrying."),
            ("Yiya", "you missed the point entirely."),
        ])
        recorded = argo_chatmine.mine_chat_log()
        self.assertEqual(recorded, 3)  # three matches recorded
        clusters = self._weakness_clusters()
        # All three roll into a SINGLE chat_weakness cluster...
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["kind"], "chat_weakness")
        # ...whose count reaches min_count=3, so diagnose()'s gate would now trip.
        self.assertEqual(clusters[0]["count"], 3)
        gate = [c for c in inc.open_clusters(min_count=3, window_hours=24 * 365)
                if c.get("kind") == "chat_weakness"]
        self.assertEqual(len(gate), 1)  # the min_count=3 gate now SEES the cluster
        # The matched phrase survives as a non-keyed sample, so the cluster stays
        # informative despite the label-only key.
        samples = clusters[0].get("samples", [])
        self.assertTrue(any("misread" in s or "missed the point" in s or
                            "misunderstood" in s for s in samples))

    def test_negative_control_substring_key_would_split_into_three(self):
        # NEGATIVE CONTROL for finding 1: prove the OLD behavior (signature = label +
        # matched substring) split the very same three turns into three count-1
        # clusters that never reach min_count. We reconstruct the old signature shape
        # against the SAME fingerprint function the real code uses; distinct
        # fingerprints == distinct clusters == gate never trips.
        old_style = [
            "user correction: misunderstood (you misunderstood)",
            "user correction: misunderstood (you completely misread)",
            "user correction: misunderstood (you missed the point)",
        ]
        old_fps = {inc._fingerprint(s) for s in old_style}
        self.assertEqual(len(old_fps), 3)  # three clusters under the old key -> count 1 each
        # The new label-only signature collapses all three to one fingerprint.
        new_fps = {inc._fingerprint("chat weakness: misunderstood") for _ in old_style}
        self.assertEqual(len(new_fps), 1)

    # --- (FINDING 2) a failed watermark advance records NOTHING (no re-bump/reopen) --
    # The round-4 fix: advance the watermark BEFORE recording, and skip recording if
    # that advance fails to persist. Before the fix, a swallowed _write_watermark
    # failure left counts bumped but the watermark behind, so the next run re-scanned
    # the same slice and re-bumped clusters (and could REOPEN a resolved one). Now a
    # write-failure short-circuits before any record_incident call.

    def test_watermark_write_failure_records_nothing(self):
        self._write_chat([
            ("Yiya", "no, that's wrong. it's Canberra."),
            ("Argo", "sorry."),
            ("Yiya", "you misunderstood the whole request."),
        ])
        # Simulate the watermark store being unwritable for this run.
        with mock.patch.object(argo_chatmine, "_write_watermark", return_value=False):
            recorded = argo_chatmine.mine_chat_log()
        # Nothing recorded, no clusters, watermark unchanged -> the next run re-scans
        # the SAME slice from scratch with no double-count to undo.
        self.assertEqual(recorded, 0)
        self.assertEqual(self._weakness_clusters(), [])
        self.assertEqual(argo_store.load_json(self.wm_path, {}), {})
        # The next (healthy) run mines the slice exactly once.
        self.assertEqual(argo_chatmine.mine_chat_log(), 2)
        self.assertEqual(argo_store.load_json(self.wm_path, {})["mined_turns"], 3)
        # A re-mine after the healthy run adds nothing (idempotent).
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)

    def test_watermark_write_failure_does_not_reopen_resolved_cluster(self):
        # The most damaging symptom finding 2 names: a re-mine of an already-counted
        # turn reopening a RESOLVED chat_weakness cluster. Prove a failed-watermark run
        # cannot touch a resolved cluster at all (it records nothing).
        self._write_chat([("Yiya", "you misunderstood me completely.")])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        key = clusters[0]["key"]
        inc.mark(key, status="resolved", belief_id="SB-chatmine")
        self.assertEqual(inc.get_cluster(key)["status"], "resolved")
        before_count = inc.get_cluster(key)["count"]
        # Append the SAME-LABEL correction again but force the watermark write to fail.
        log = argo_store.load_json(self.chat_path, [])
        log.append({"ts": "2026-06-20T02:00:00Z", "chat_id": "1",
                    "role": "Yiya", "text": "you misread it again, same problem."})
        argo_store.save_json(self.chat_path, log)
        with mock.patch.object(argo_chatmine, "_write_watermark", return_value=False):
            self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        # The resolved cluster is untouched: still resolved, count unchanged.
        c = inc.get_cluster(key)
        self.assertEqual(c["status"], "resolved")
        self.assertEqual(c["count"], before_count)
        self.assertNotIn("recurred_after_fix", c)

    # --- NEW SIGNAL 1: Argo self-bluff (a claimed completed action on an Argo turn) --
    # These mine the INVERSE role from the user-correction signals: a turn whose role
    # IS "Argo". A flat "i just opened the PR" is frequently a phantom claim.

    def test_argo_bluff_claimed_action_fires(self):
        self._write_chat([
            ("Yiya", "can you open the PR for the fix?"),
            ("Argo", "i just opened the PR for you."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["kind"], "chat_weakness")
        self.assertEqual(clusters[0]["fingerprint"], "chat weakness: argo_bluff")

    def test_argo_bluff_does_not_fire_on_user_turn_or_offer(self):
        # A USER asking Argo to do it, and Argo OFFERING (not claiming done), are clean.
        self._write_chat([
            ("Yiya", "i just opened the PR, can you review it?"),
            ("Argo", "i can open a follow-up PR if you want."),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(self._weakness_clusters(), [])

    # --- NEW SIGNAL 2: Argo-voiced explicit failure (on an Argo turn) --------------

    def test_argo_failure_phrase_fires(self):
        self._write_chat([
            ("Yiya", "pull the latest from that page."),
            ("Argo", "i couldn't fetch that page, sorry."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["fingerprint"], "chat weakness: argo_failure")

    def test_argo_failure_no_access_fires(self):
        self._write_chat([
            ("Argo", "i don't have access to that file."),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        self.assertEqual(self._weakness_clusters()[0]["fingerprint"],
                         "chat weakness: argo_failure")

    # --- NEW SIGNAL 3: confused exchange (same user text in consecutive user turns) -

    def test_repeated_consecutive_user_turn_fires_confusion(self):
        self._write_chat([
            ("Yiya", "what's the ETA on the deploy?"),
            ("Yiya", "what's the ETA on the deploy?"),
        ])
        self.assertGreaterEqual(argo_chatmine.mine_chat_log(), 1)
        clusters = self._weakness_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["fingerprint"], "chat weakness: confused_repeat")

    def test_repeat_with_argo_answer_between_does_not_fire(self):
        # Re-asking AFTER Argo answered is a normal follow-up, not a confused re-ask:
        # the intervening Argo turn breaks the consecutive-user streak.
        self._write_chat([
            ("Yiya", "what's the ETA on the deploy?"),
            ("Argo", "tomorrow morning."),
            ("Yiya", "what's the ETA on the deploy?"),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(self._weakness_clusters(), [])

    # --- NEGATIVE CONTROL holds WITH the new signals active ------------------------
    # Re-asserts test_a_clean_transcript's invariant now that Argo-turn + confusion
    # signals are live: an Argo turn that says "i ... created ..."-shaped words in a
    # benign way, and distinct user questions, still record NOTHING.

    def test_clean_transcript_still_zero_with_new_signals(self):
        self._write_chat([
            ("Yiya", "can you summarize the latest AI papers?"),
            ("Argo", "sure, here are three i found useful."),
            ("Yiya", "what went wrong with the launch last week?"),
            ("Argo", "the rollout slipped a day; no errors though."),
            ("Yiya", "is it wrong to ship on a friday?"),
            ("Argo", "i can open a doc on on-call tradeoffs if you want."),
            ("Yiya", "love it, you nailed it."),
        ])
        self.assertEqual(argo_chatmine.mine_chat_log(), 0)
        self.assertEqual(self._weakness_clusters(), [])

    # --- NEW-SIGNAL IDEMPOTENCY: re-mining the same log files nothing the 2nd time --
    # The committed test_remine_same_log_records_zero_new only covers the OLD user-
    # correction signals; this locks the same watermark guarantee for all THREE new
    # signals (Argo bluff, Argo failure, confused re-ask) together.

    def test_remine_new_signals_records_zero_new(self):
        self._write_chat([
            ("Argo", "i just opened the PR for you."),          # argo_bluff
            ("Yiya", "fetch that page please."),
            ("Argo", "i couldn't fetch that page, sorry."),     # argo_failure
            ("Yiya", "what's the ETA on the deploy?"),
            ("Yiya", "what's the ETA on the deploy?"),          # confused_repeat
        ])
        first = argo_chatmine.mine_chat_log()
        self.assertEqual(first, 3)  # one incident per new signal
        fps = {c["fingerprint"] for c in self._weakness_clusters()}
        self.assertEqual(fps, {"chat weakness: argo_bluff",
                               "chat weakness: argo_failure",
                               "chat weakness: confused_repeat"})
        before = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        # Second pass over the UNCHANGED log: the watermark gates all three new
        # signals, so nothing new is filed and no cluster count is bumped.
        second = argo_chatmine.mine_chat_log()
        self.assertEqual(second, 0)
        after = {c["fingerprint"]: c["count"] for c in self._weakness_clusters()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
