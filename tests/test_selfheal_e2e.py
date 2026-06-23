"""End-to-end self-improvement loop (the spine): observe -> diagnose -> propose -> verify
-> confirm -> resolve, plus post-resolution recurrence reopening the cluster.

Drives the REAL machinery across all three modules (argo_incidents, argo_diagnose,
argo_mcp_server) with only the two genuinely-external steps faked: the fix-authoring model
call and the GitHub writes/CI read. No network, no LLM, no real data files.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_diagnose as dg
import argo_incidents as inc
import argo_mcp_server as srv
import argo_self


class SelfHealEndToEndTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.enterContext(mock.patch.object(dg, "PROPOSALS_PATH", base / "prop.json"))
        self.enterContext(mock.patch.object(argo_self, "SELF_PATH", base / "self.json"))
        self.enterContext(mock.patch.object(srv, "PENDING_HEAL_PATH", base / "pending.json"))
        self.sent = []
        self.enterContext(mock.patch.object(dg, "_send", lambda t: self.sent.append(t) or True))

    def _belief_status(self, bid):
        return next(b["status"] for b in argo_self.get_self_beliefs() if b["id"] == bid)

    def test_full_loop(self):
        # 1. OBSERVE: the same failure recurs three times.
        for _ in range(3):
            inc.record_incident("phantom_send", "reply claimed a proposal but no tool fired")

        # 2. DIAGNOSE: confident -> seeds an issue belief, stages a fix (REAL staging),
        #    sends one FIX nudge. The model call is the only faked step.
        canned = {"diagnosis": "the phantom guard returns before recording",
                  "suspected_files": ["src/argo_webhook.py"],
                  "suggestion": "record the incident before returning the honest reply",
                  "confident_enough_to_propose": True}
        with mock.patch.object(dg, "_diagnose_cluster", return_value=canned):
            res = dg.diagnose()
        self.assertTrue(res["confident"])
        key, bid = res["key"], res["belief_id"]
        self.assertEqual(self._belief_status(bid), "unverified")
        self.assertTrue((srv.PENDING_HEAL_PATH).exists())     # a fix is staged

        # 3. PROPOSE (user replies FIX): run the REAL propose path -- author + resolve
        #    (faked: the model now drafts surgical edits, _resolve_edits applies them), the
        #    repro+wire gate (real), open PR (faked), record in the ledger (real).
        authored = {
            "src/argo_webhook.py": "# fixed: record the incident first\n",
            "tests/test_phantom_repro.py": (
                "import argo_webhook\n"
                "def test_guard_records():\n"
                "    assert hasattr(argo_webhook, 'handle_update')\n")}
        fake_pr = (True, {"pr_number": 42, "url": "http://pr/42",
                          "head_sha": "sha1", "branch": "argo/fix"})
        with mock.patch.object(
                srv, "_author_fix_edits",
                return_value=[{"path": "src/argo_webhook.py", "old": "a", "new": "b"}]), \
             mock.patch.object(srv, "_resolve_edits", return_value=(authored, None)), \
             mock.patch.object(srv, "_open_pr", return_value=fake_pr):
            msg = srv.run_pending_heal()
        self.assertIn("http://pr/42", msg)
        prop = dg._load_proposals()[0]
        self.assertEqual(prop["pr_number"], 42)
        self.assertEqual(prop["belief_id"], bid)
        self.assertEqual(prop["incident_key"], key)
        self.assertFalse(srv.PENDING_HEAL_PATH.exists())      # one-shot, cleared

        # Backdate the original incident so the merge is genuinely AFTER it.
        inc.mark(key, last_seen="2000-01-01T00:00:00Z")

        # 4. VERIFY: CI green and merged -> start the post-deploy watch, do NOT resolve.
        with mock.patch.object(dg, "_check_ci", return_value={
                "merged": True, "state": "closed", "merged_at": "2000-02-01T00:00:00Z",
                "head_sha": "sha1", "ci_conclusion": "success"}):
            dg.verify_open_proposals()
        self.assertEqual(self._belief_status(bid), "unverified")  # still not resolved
        self.assertEqual(dg._load_proposals()[0]["deploy_watch_until"],
                         "2000-02-02T00:00:00Z")

        # 5. CONFIRM: watch window elapsed, no recurrence -> resolve WITH evidence.
        dg.confirm_deployed()
        self.assertEqual(self._belief_status(bid), "resolved")
        self.assertEqual(inc.get_cluster(key)["status"], "resolved")

        # 6. POST-RESOLUTION RECURRENCE: the ledger is its own verifier -- the same
        #    failure happening again reopens the cluster, flagged as a fix that didn't hold.
        inc.record_incident("phantom_send", "reply claimed a proposal but no tool fired")
        c = inc.get_cluster(key)
        self.assertEqual(c["status"], "open")
        self.assertTrue(c["recurred_after_fix"])


if __name__ == "__main__":
    unittest.main()
