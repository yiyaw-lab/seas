"""Verify + confirm tests (argo_diagnose): closing the loop on a proposed fix.

This is the cure for "fixes that don't fix." A belief is only marked resolved after the
PR's CI is green AND it merges AND a quiet post-deploy window passes with zero recurrence
in the incident ledger. CI red, an unreadable token, or a recurrence all keep the belief
unresolved (refuted) -- a confidently-wrong fix can never launder into a false "fixed."

GitHub is faked (dg._check_ci patched); timestamps are fixed so the post-deploy window is
deterministic. No network, no LLM.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_diagnose as dg
import argo_incidents as inc
import argo_self


class VerifyConfirmTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.enterContext(mock.patch.object(dg, "PROPOSALS_PATH", base / "prop.json"))
        self.enterContext(mock.patch.object(argo_self, "SELF_PATH", base / "self.json"))
        self.sent = []
        self.enterContext(mock.patch.object(dg, "_send", lambda t: self.sent.append(t) or True))

        # One incident cluster + its issue belief + a proposal joining them.
        self.key = inc.record_incident("phantom_send", "claimed but no tool fired")
        inc.mark(self.key, status="diagnosed")
        self.bid = argo_self.add_self_belief("phantom guard misfires", kind="issue",
                                             source="diagnosis")
        inc.mark(self.key, belief_id=self.bid)
        dg.append_proposal(42, "http://pr/42", self.bid, self.key, head_sha="sha1")

    def _belief(self):
        return next(b for b in argo_self.get_self_beliefs() if b["id"] == self.bid)

    def _proposal(self):
        return dg._load_proposals()[0]

    def _set_proposal(self, **fields):
        items = dg._load_proposals()
        items[0].update(fields)
        dg._save_proposals(items)

    # --- verify_open_proposals ---------------------------------------------

    def test_ci_failure_refutes_and_reopens(self):
        with mock.patch.object(dg, "_check_ci", return_value={
                "merged": False, "state": "open", "merged_at": None,
                "head_sha": "sha1", "ci_conclusion": "failure"}):
            dg.verify_open_proposals()
        b = self._belief()
        self.assertEqual(b["status"], "unverified")           # NOT resolved
        self.assertTrue(b["refutations"])                     # refuting evidence recorded
        self.assertEqual(inc.get_cluster(self.key)["status"], "open")  # reopened
        self.assertTrue(self._proposal()["ci_failed"])

    def test_ci_green_merged_starts_watch_does_not_resolve(self):
        with mock.patch.object(dg, "_check_ci", return_value={
                "merged": True, "state": "closed", "merged_at": "2000-01-01T00:00:00Z",
                "head_sha": "sha1", "ci_conclusion": "success"}):
            dg.verify_open_proposals()
        self.assertEqual(self._belief()["status"], "unverified")   # still not resolved
        p = self._proposal()
        self.assertTrue(p["merged"])
        self.assertEqual(p["deploy_watch_until"], "2000-01-02T00:00:00Z")  # merged + 24h

    def test_ci_unreadable_token_never_resolves(self):
        with mock.patch.object(dg, "_check_ci", return_value={
                "merged": False, "state": "unknown", "merged_at": None,
                "head_sha": None, "ci_conclusion": "unknown"}):
            dg.verify_open_proposals()
        b = self._belief()
        self.assertEqual(b["status"], "unverified")
        self.assertFalse(b["refutations"])                    # no fabricated verdict
        self.assertFalse(self._proposal()["ci_failed"])

    # --- external review surfacing (Cursor Bugbot) -------------------------

    _OPEN_CI = {"merged": False, "state": "open", "merged_at": None,
                "head_sha": "sha1", "ci_conclusion": "pending"}

    def test_new_bot_review_is_surfaced(self):
        # Body carries raw markdown + an em dash; the surfaced text must be sanitized
        # (Argo's plain-text output rule), and the comment id recorded as seen.
        rev = {"summary": "found 1 potential issue", "findings": [
            {"id": 11, "path": "src/x.py", "line": 5,
             "body": "possible null deref — see **here**"}]}
        with mock.patch.object(dg, "_check_ci", return_value=self._OPEN_CI), \
             mock.patch.object(dg, "_check_reviews", return_value=rev):
            dg.verify_open_proposals()
        msg = next(s for s in self.sent if "cursorbot" in s)
        self.assertIn("PR #42", msg)
        self.assertNotIn("—", msg)        # em dash stripped
        self.assertNotIn("**", msg)       # markdown stripped
        self.assertEqual(self._proposal()["seen_review_ids"], [11])

    def test_seen_bot_finding_not_resurfaced(self):
        # The stored seen set is threaded to _check_reviews; with no fresh findings
        # back, no new Telegram message goes out (re-poll is silent).
        self._set_proposal(seen_review_ids=[11])
        captured = {}

        def fake_reviews(n, seen):
            captured["seen"] = seen
            return {"summary": "no new issues", "findings": []}

        with mock.patch.object(dg, "_check_ci", return_value=self._OPEN_CI), \
             mock.patch.object(dg, "_check_reviews", side_effect=fake_reviews):
            dg.verify_open_proposals()
        self.assertEqual(captured["seen"], [11])               # baseline threaded through
        self.assertFalse(any("cursorbot" in s for s in self.sent))

    def test_ci_failed_pr_still_surfaces_new_review(self):
        # A parked (ci_failed) but still-open PR must still get NEW bot findings
        # surfaced, and its settled CI verdict must NOT be re-polled.
        self._set_proposal(ci_failed=True)
        rev = {"summary": "found 1", "findings": [
            {"id": 21, "path": "src/y.py", "line": 1, "body": "resource leak"}]}
        ci_mock = mock.Mock(side_effect=AssertionError("CI re-polled on a parked PR"))
        with mock.patch.object(dg, "_check_ci", ci_mock), \
             mock.patch.object(dg, "_check_reviews", return_value=rev):
            dg.verify_open_proposals()
        self.assertTrue(any("cursorbot" in s for s in self.sent))
        self.assertEqual(self._proposal()["seen_review_ids"], [21])

    # --- confirm_deployed ---------------------------------------------------

    def test_no_recurrence_resolves_belief(self):
        # merged in the past, incident last seen BEFORE the merge -> no recurrence.
        inc.mark(self.key, last_seen="1999-01-01T00:00:00Z")
        self._set_proposal(merged=True, merged_at="2000-01-01T00:00:00Z",
                           deploy_watch_until="2000-01-02T00:00:00Z")
        dg.confirm_deployed()
        self.assertEqual(self._belief()["status"], "resolved")
        self.assertEqual(inc.get_cluster(self.key)["status"], "resolved")
        self.assertTrue(self._proposal()["resolved"])

    def test_recurrence_during_watch_refutes_and_reopens(self):
        # incident last seen AFTER the merge -> the fix didn't hold.
        inc.mark(self.key, last_seen="2000-06-01T00:00:00Z")
        self._set_proposal(merged=True, merged_at="2000-01-01T00:00:00Z",
                           deploy_watch_until="2000-01-02T00:00:00Z")
        dg.confirm_deployed()
        b = self._belief()
        self.assertEqual(b["status"], "unverified")           # not resolved
        self.assertTrue(b["refutations"])
        self.assertEqual(inc.get_cluster(self.key)["status"], "open")

    def test_watch_not_elapsed_holds(self):
        # deploy_watch_until far in the future -> still watching, no decision yet.
        self._set_proposal(merged=True, merged_at="2000-01-01T00:00:00Z",
                           deploy_watch_until="2999-01-01T00:00:00Z")
        dg.confirm_deployed()
        self.assertEqual(self._belief()["status"], "unverified")
        self.assertFalse(self._proposal()["resolved"])


if __name__ == "__main__":
    unittest.main()
