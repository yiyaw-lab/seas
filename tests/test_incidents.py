"""Incident-ledger tests (argo_incidents): the 'observe' layer of the self-improvement loop.

Pure -- no network, no LLM, no real data/*.json. The path constant is patched to a temp
file (the tests/test_self.py idiom). Covers the rollup/fingerprint dedup, the never-raise
contract, open_clusters' min_count + window gating, recurrence-reopens-resolved, mute, and
prune -- the behaviours the diagnostic loop depends on.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_incidents as inc
import argo_store


class IncidentLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "incidents.json"
        patcher = mock.patch.object(inc, "INCIDENTS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- fingerprint rollup -------------------------------------------------

    def test_near_identical_signatures_roll_into_one_cluster(self):
        inc.record_incident("delivery_failure", "sendMessage failed: 503 at 14:03")
        key = inc.record_incident("delivery_failure", "sendMessage failed: 502 at 17:55")
        clusters = inc.open_clusters(min_count=1)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 2)
        self.assertEqual(clusters[0]["key"], key)

    def test_fingerprint_strips_digits_uuids_urls_and_hex(self):
        self.assertEqual(
            inc._fingerprint("error 12345 abc"), inc._fingerprint("error 999 abc"))
        self.assertEqual(
            inc._fingerprint("see https://x.test/a/1 now"),
            inc._fingerprint("see https://y.test/b/2 now"))
        u1 = "12345678-1234-1234-1234-123456789abc"
        u2 = "abcdef00-0000-0000-0000-000000000000"
        self.assertEqual(inc._fingerprint(f"job {u1}"), inc._fingerprint(f"job {u2}"))
        self.assertEqual(  # 40-char shas collapse
            inc._fingerprint("sha " + "a" * 40), inc._fingerprint("sha " + "b" * 40))

    def test_distinct_kinds_stay_separate_even_with_same_signature(self):
        inc.record_incident("tool_error", "boom")
        inc.record_incident("model_failure", "boom")
        self.assertEqual(len(inc.open_clusters(min_count=1)), 2)

    def test_unknown_kind_coerced_to_other(self):
        key = inc.record_incident("not_a_real_kind", "whatever")
        self.assertTrue(key.startswith("other|"))

    # --- seen_since (kind-level recurrence, used by the prediction scorer) ---

    def test_seen_since_true_only_for_kind_seen_after_cutoff(self):
        inc.record_incident("scheduler_task_error", "boom")
        self.assertTrue(inc.seen_since("scheduler_task_error", "2020-01-01T00:00:00Z"))
        self.assertFalse(inc.seen_since("scheduler_task_error", "2099-01-01T00:00:00Z"))
        self.assertFalse(inc.seen_since("tool_error", "2020-01-01T00:00:00Z"))

    def test_seen_since_empty_store_is_false_and_never_raises(self):
        self.assertFalse(inc.seen_since("tool_error", "2020-01-01T00:00:00Z"))

    def test_samples_capped_to_three_newest_first(self):
        for i in range(5):
            inc.record_incident("tool_error", "same sig", f"sample{i}")
        c = inc.open_clusters(min_count=1)[0]
        self.assertEqual(len(c["samples"]), inc.MAX_SAMPLES)
        self.assertEqual(c["samples"][0], "sample4")  # newest first

    # --- never-raise contract ----------------------------------------------

    def test_record_incident_returns_none_and_never_raises_on_store_error(self):
        with mock.patch.object(argo_store, "save_json",
                               side_effect=OSError("disk full")):
            self.assertIsNone(inc.record_incident("tool_error", "x"))  # no exception

    # --- open_clusters gating ----------------------------------------------

    def test_open_clusters_respects_min_count(self):
        inc.record_incident("tool_error", "once")
        self.assertEqual(inc.open_clusters(min_count=3), [])
        inc.record_incident("tool_error", "once")
        inc.record_incident("tool_error", "once")
        self.assertEqual(len(inc.open_clusters(min_count=3)), 1)

    def test_open_clusters_respects_window(self):
        key = inc.record_incident("tool_error", "stale")
        inc.mark(key, last_seen="2000-01-01T00:00:00Z")
        self.assertEqual(inc.open_clusters(min_count=1, window_hours=24), [])

    def test_open_clusters_excludes_non_open_status(self):
        key = inc.record_incident("tool_error", "diagnosed already")
        inc.mark(key, status="diagnosed")
        self.assertEqual(inc.open_clusters(min_count=1), [])

    # --- recurrence reopens a resolved cluster -----------------------------

    def test_recurrence_reopens_resolved_with_flag(self):
        key = inc.record_incident("phantom_send", "claimed but no tool")
        inc.mark(key, status="resolved", belief_id="SB-001")
        self.assertEqual(inc.open_clusters(min_count=1), [])
        inc.record_incident("phantom_send", "claimed but no tool")  # it came back
        clusters = inc.open_clusters(min_count=1)
        self.assertEqual(len(clusters), 1)
        self.assertTrue(clusters[0]["recurred_after_fix"])
        self.assertEqual(clusters[0]["belief_id"], "SB-001")  # belief preserved

    def test_muted_cluster_hidden_until_window_passes(self):
        key = inc.record_incident("tool_error", "noisy")
        inc.record_incident("tool_error", "noisy")
        inc.mark(key, status="muted", muted_until="2999-01-01T00:00:00Z")
        self.assertEqual(inc.open_clusters(min_count=1), [])
        # a past mute window: the next incident reopens it
        inc.mark(key, muted_until="2000-01-01T00:00:00Z")
        inc.record_incident("tool_error", "noisy")
        self.assertEqual(len(inc.open_clusters(min_count=1)), 1)

    # --- recurred_since + prune --------------------------------------------

    def test_recurred_since_is_strict(self):
        key = inc.record_incident("tool_error", "x")
        last = inc.get_cluster(key)["last_seen"]
        self.assertTrue(inc.recurred_since(key, "2000-01-01T00:00:00Z"))
        self.assertFalse(inc.recurred_since(key, last))           # strict >
        self.assertFalse(inc.recurred_since(key, "2999-01-01T00:00:00Z"))

    def test_prune_drops_old_resolved_keeps_open(self):
        old = inc.record_incident("tool_error", "old resolved")
        inc.mark(old, status="resolved", last_seen="2000-01-01T00:00:00Z")
        keep = inc.record_incident("tool_error", "still open")
        inc.mark(keep, last_seen="2000-01-01T00:00:00Z")  # old but open -> kept
        self.assertEqual(inc.prune(max_age_days=14), 1)
        self.assertIsNone(inc.get_cluster(old))
        self.assertIsNotNone(inc.get_cluster(keep))

    # --- reserved meta keys are not clusters -------------------------------

    def test_meta_keys_are_not_treated_as_clusters(self):
        inc.set_meta("_diagnose_meta", {"last_nudge_date": "2026-06-10"})
        inc.record_incident("tool_error", "real")
        clusters = inc.open_clusters(min_count=1)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(inc.get_meta("_diagnose_meta")["last_nudge_date"], "2026-06-10")

    # --- detail_report: the operator-facing samples behind each count ------

    def test_detail_report_empty_store(self):
        self.assertEqual(inc.detail_report(), "No open incident clusters.")

    def test_detail_report_surfaces_failing_tool_name_from_signature(self):
        # Record the way production does (_record_tool_error, argo_observe.py): the tool
        # NAME lives in the SIGNATURE (-> fingerprint), and the SAMPLE is the bare
        # message with NO name. detail_report must still surface which tool failed --
        # so it prints the fingerprint. (This FAILS before the fingerprint fix: the
        # header would be a nameless "tool_error".)
        name, detail = "web_fetch", "Error while communicating with MCP server"
        inc.record_incident("tool_error", f"{name}: {detail}", str(detail)[:200])
        report = inc.detail_report()
        self.assertIn("web_fetch", report)              # the failing tool, from the fingerprint
        self.assertIn("communicating with mcp server", report.lower())  # and the message

    def test_detail_report_disambiguates_two_tools_with_same_message(self):
        # The core value: two DIFFERENT tools failing with the SAME generic connector
        # message must not render as byte-identical lines -- the bug the naive
        # (header = bare kind) version had.
        msg = "Error while communicating with MCP server"
        inc.record_incident("tool_error", f"web_fetch: {msg}", msg)
        inc.record_incident("tool_error", f"github_read_file: {msg}", msg)
        report = inc.detail_report()
        self.assertIn("web_fetch", report)
        self.assertIn("github_read_file", report)

    def test_detail_report_shows_low_rate_cluster_the_funnel_skips(self):
        # Default min_count=1 surfaces a single occurrence (below the diagnose funnel's
        # min_count=3), matching what the operator sees on /health -- closing the
        # see-a-count-but-not-the-cause gap for exactly the low-rate clusters that
        # never auto-triage. Recorded production-faithfully (name in the signature).
        inc.record_incident("tool_error", "github_read_file: 502 Bad Gateway", "502 Bad Gateway")
        self.assertEqual(inc.open_clusters(min_count=3), [])      # funnel would skip
        self.assertIn("github_read_file", inc.detail_report())   # detail still shows the tool

    def test_detail_report_respects_limit(self):
        for sig in ("alpha", "bravo", "charlie", "delta"):  # distinct fingerprints
            inc.record_incident("tool_error", sig, f"sample {sig}")
        # each cluster header starts "tool_error [<fingerprint>] x<count>"
        self.assertEqual(inc.detail_report(limit=2).count("tool_error ["), 2)

    def test_detail_report_shows_triage_belief_and_pr(self):
        key = inc.record_incident("tool_error", "triaged one", "propose_change: 422")
        inc.mark(key, belief_id="SB-007", pr_number=42)
        report = inc.detail_report()
        self.assertIn("belief SB-007", report)
        self.assertIn("PR #42", report)

    def test_detail_report_never_raises_on_store_error(self):
        with mock.patch.object(inc, "_load", side_effect=OSError("disk gone")):
            self.assertEqual(inc.detail_report(), "No open incident clusters.")


if __name__ == "__main__":
    unittest.main()
