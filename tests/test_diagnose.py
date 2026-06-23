"""Diagnose-funnel tests (argo_diagnose.diagnose): the proactive half of the loop.

Four free gates run before any model call; only a real, recent, not-already-worked
recurrence escalates, at most once a day, and a guess is reported (not turned into a
rigged PR). These tests lock each gate. The model call, Telegram send, and fix staging
are patched so the funnel logic tests hermetically -- no LLM, no network, no MCP.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_diagnose as dg
import argo_github
import argo_incidents as inc
import argo_observe as observe
import argo_self


class DiagnoseFunnelTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.enterContext(mock.patch.object(dg, "PROPOSALS_PATH", base / "prop.json"))
        self.enterContext(mock.patch.object(argo_self, "SELF_PATH", base / "self.json"))
        self.sent = []
        self.staged = []
        self.enterContext(mock.patch.object(dg, "_send", lambda t: self.sent.append(t) or True))
        self.enterContext(mock.patch.object(dg, "_stage_fix", lambda p: self.staged.append(p)))

    def _record(self, n, kind="phantom_send", sig="claimed but no tool fired"):
        for _ in range(n):
            inc.record_incident(kind, sig)

    def test_gate_a_below_threshold_no_model_call(self):
        self._record(2)  # MIN_COUNT is 3
        with mock.patch.object(dg, "_diagnose_cluster",
                               side_effect=AssertionError("model must not run")) as m:
            res = dg.diagnose()
        self.assertFalse(res["acted"])
        m.assert_not_called()

    def test_gate_c_daily_nudge_cap_skips_before_model(self):
        self._record(5)
        inc.set_meta(dg._META_KEY, {"last_nudge_date": dg._now().strftime("%Y-%m-%d"),
                                    "nudges_today": dg.MAX_NUDGES_PER_DAY})
        with mock.patch.object(dg, "_diagnose_cluster",
                               side_effect=AssertionError("model must not run")) as m:
            res = dg.diagnose()
        self.assertFalse(res["acted"])
        self.assertIn("budget", res["reason"])
        m.assert_not_called()

    def test_not_confident_is_report_only_no_staged_fix(self):
        self._record(3)
        canned = {"diagnosis": "the reply guard misfires", "suspected_files": [],
                  "suggestion": "x", "confident_enough_to_propose": False}
        with mock.patch.object(dg, "_diagnose_cluster", return_value=canned):
            res = dg.diagnose()
        self.assertTrue(res["acted"])
        self.assertFalse(res["confident"])
        self.assertEqual(self.staged, [])                    # no PR offered for a guess
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(argo_self.get_self_beliefs(kind="issue")), 1)

    def test_suspected_file_not_in_repo_is_report_only(self):
        self._record(3)
        canned = {"diagnosis": "guess", "suspected_files": ["src/nope_xyz.py"],
                  "suggestion": "x", "confident_enough_to_propose": True}
        with mock.patch.object(dg, "_diagnose_cluster", return_value=canned):
            res = dg.diagnose()
        self.assertFalse(res["confident"])
        self.assertEqual(self.staged, [])

    def test_confident_stages_one_fix_and_nudges_once(self):
        self._record(4)
        canned = {"diagnosis": "phantom guard returns early",
                  "suspected_files": ["src/argo_webhook.py"],
                  "suggestion": "record the incident before returning",
                  "confident_enough_to_propose": True}
        with mock.patch.object(dg, "_diagnose_cluster", return_value=canned):
            res = dg.diagnose()
        self.assertTrue(res["confident"])
        self.assertEqual(len(self.staged), 1)
        self.assertEqual(self.staged[0]["suspected_files"], ["src/argo_webhook.py"])
        self.assertTrue(self.staged[0]["incident_key"].startswith("phantom_send|"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("FIX", self.sent[0])
        self.assertEqual(len(argo_self.get_self_beliefs(kind="issue")), 1)
        # cluster moved out of 'open' so it won't re-nudge tomorrow
        self.assertEqual(inc.get_cluster(self.staged[0]["incident_key"])["status"], "diagnosed")
        # nudge budget consumed
        self.assertEqual(dg._nudge_budget_left(), 0)

    def test_gate_b_skips_cluster_with_active_proposal(self):
        self._record(3)
        key = inc.open_clusters(min_count=1)[0]["key"]
        dg.append_proposal(99, "http://pr/99", "SB-001", key)  # open PR in flight
        with mock.patch.object(dg, "_diagnose_cluster",
                               side_effect=AssertionError("model must not run")) as m:
            res = dg.diagnose()
        self.assertFalse(res["acted"])
        m.assert_not_called()

    def test_gate_b_allows_rediagnosis_after_ci_failure(self):
        # A cluster reopened after its fix failed CI (terminal proposal) must be eligible.
        self._record(3)
        key = inc.open_clusters(min_count=1)[0]["key"]
        p = dg.append_proposal(98, "http://pr/98", "SB-001", key)
        dg._save_proposals([{**p, "ci_failed": True}])  # terminal -> not in flight
        canned = {"diagnosis": "still broken", "suspected_files": ["src/argo_webhook.py"],
                  "suggestion": "try again", "confident_enough_to_propose": True}
        with mock.patch.object(dg, "_diagnose_cluster", return_value=canned):
            res = dg.diagnose()
        self.assertTrue(res["acted"])  # re-diagnosed, not skipped


class ConfirmDeployedTest(unittest.TestCase):
    """confirm_deployed closes the post-merge watch: incident-keyed proposals are
    graded against the incident ledger; null-key ones (evolution upgrades) resolve
    honestly without claiming a recurrence check that never ran."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.enterContext(mock.patch.object(dg, "PROPOSALS_PATH", base / "prop.json"))
        self.enterContext(mock.patch.object(argo_self, "SELF_PATH", base / "self.json"))
        self.sent = []
        self.enterContext(mock.patch.object(dg, "_send", lambda t: self.sent.append(t) or True))

    def _merged_proposal(self, bid, incident_key, n=9):
        dg.append_proposal(n, f"http://pr/{n}", bid, incident_key)
        items = dg._load_proposals()
        items[0].update(merged=True, merged_at="2026-06-01T00:00:00Z",
                        deploy_watch_until="2026-06-02T00:00:00Z")  # window elapsed
        dg._save_proposals(items)

    def test_null_incident_key_resolves_without_recurrence_claim(self):
        bid = argo_self.add_self_belief("adopting x improves me", kind="capability",
                                        source="evolution")
        self._merged_proposal(bid, None)
        with mock.patch.object(inc, "recurred_since",
                               side_effect=AssertionError("no incident to check")):
            dg.confirm_deployed()
        p = dg._load_proposals()[0]
        self.assertTrue(p["resolved"])
        self.assertIs(p["held"], True)  # stamped for downstream outcome sync
        belief = next(b for b in argo_self.get_self_beliefs() if b["id"] == bid)
        self.assertEqual(belief["status"], "resolved")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("dated prediction", self.sent[0])  # honest evolution wording
        self.assertNotIn("no recurrence", self.sent[0])

    def test_incident_recurrence_refutes_and_reopens(self):
        bid = argo_self.add_self_belief("i drop messages", kind="capability")
        key = inc.record_incident("phantom_send", "claimed but no tool fired")
        self._merged_proposal(bid, key)  # incident seen after merged_at -> recurred
        dg.confirm_deployed()
        p = dg._load_proposals()[0]
        self.assertTrue(p["resolved"])
        self.assertIs(p["held"], False)
        belief = next(b for b in argo_self.get_self_beliefs() if b["id"] == bid)
        self.assertTrue(belief.get("refutations"))
        self.assertIn("didn't hold", self.sent[0])
        self.assertEqual(inc.get_cluster(key)["status"], "open")


class DiagnoseClusterTest(unittest.TestCase):
    """_diagnose_cluster: structured-output enforcement on the Anthropic path, a safe
    fallback if the API rejects the schema, and the measurable incident on a parse drop."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        self.enterContext(mock.patch.object(dg, "_resolve_model", lambda: "claude-sonnet-4-6"))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY"}))
        self.cluster = {"kind": "phantom_send", "count": 4,
                        "fingerprint": "fp", "samples": ["s"]}

    _GOOD = ('{"diagnosis":"d","suspected_files":[],"suggestion":"s",'
             '"confident_enough_to_propose":false}')

    def test_structured_schema_is_enforced(self):
        captured = {}

        def fake_chat(system, messages, model, **kw):
            captured.update(kw)
            return self._GOOD
        with mock.patch.object(observe, "chat_with_mcp", fake_chat):
            out = dg._diagnose_cluster(self.cluster)
        self.assertEqual(out["diagnosis"], "d")
        self.assertEqual(captured.get("output_schema"), dg._DIAGNOSE_SCHEMA)

    def test_falls_back_to_plain_when_structured_rejected(self):
        calls = []

        def fake_chat(system, messages, model, **kw):
            calls.append(kw)
            if "output_schema" in kw:
                raise RuntimeError("400 unsupported output_config shape")
            return self._GOOD
        with mock.patch.object(observe, "chat_with_mcp", fake_chat):
            out = dg._diagnose_cluster(self.cluster)
        self.assertEqual(out["diagnosis"], "d")          # degraded, not dark
        self.assertEqual(len(calls), 2)
        self.assertIn("output_schema", calls[0])
        self.assertNotIn("output_schema", calls[1])

    def test_parse_failure_records_measurable_incident(self):
        with mock.patch.object(observe, "chat_with_mcp",
                               lambda *a, **k: "sorry, prose not json"):
            out = dg._diagnose_cluster(self.cluster)
        self.assertIsNone(out)
        clusters = inc.open_clusters(min_count=1, window_hours=999)
        self.assertTrue(any(c["kind"] == "model_failure"
                            and "parse" in (c.get("fingerprint") or "").lower()
                            for c in clusters))

    def test_empty_reply_records_no_incident(self):
        # An empty reply is a different (already-handled) class -- don't double-count it.
        with mock.patch.object(observe, "chat_with_mcp", lambda *a, **k: ""):
            self.assertIsNone(dg._diagnose_cluster(self.cluster))
        self.assertEqual(inc.open_clusters(min_count=1, window_hours=999), [])

    def test_code_context_is_added_to_prompt_when_sample_names_a_file(self):
        # A sample with a source path/line -> the failing code (via the CONFINED reader)
        # lands in the diagnose prompt, so the model reasons from real bytes not just a log.
        cluster = {"kind": "tool_error", "count": 4, "fingerprint": "fp",
                   "samples": ['File "src/argo_observe.py", line 5, in chat']}
        seen = {}

        def fake_chat(system, messages, model, **kw):
            seen["prompt"] = messages[0]["content"]
            return self._GOOD
        with mock.patch.object(argo_github, "read_local_source",
                               return_value="FIXTURE_SNIPPET_42"), \
             mock.patch.object(observe, "chat_with_mcp", fake_chat):
            dg._diagnose_cluster(cluster)
        self.assertIn("FIXTURE_SNIPPET_42", seen["prompt"])
        self.assertIn("src/argo_observe.py", seen["prompt"])

    def test_code_context_degrades_without_crashing_on_read_error(self):
        # A read failure must NOT crash diagnose(); the prompt falls back to filename-only.
        cluster = {"kind": "tool_error", "count": 4, "fingerprint": "fp",
                   "samples": ['File "src/argo_observe.py", line 5']}
        seen = {}

        def fake_chat(system, messages, model, **kw):
            seen["prompt"] = messages[0]["content"]
            return self._GOOD
        with mock.patch.object(argo_github, "read_local_source",
                               side_effect=OSError("disk gone")), \
             mock.patch.object(observe, "chat_with_mcp", fake_chat):
            out = dg._diagnose_cluster(cluster)
        self.assertEqual(out["diagnosis"], "d")        # no crash
        self.assertIn("(none found)", seen["prompt"])  # degraded section


if __name__ == "__main__":
    unittest.main()
