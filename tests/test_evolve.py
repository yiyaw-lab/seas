"""Frontier-evolution funnel tests (argo_evolve): the proactive upgrade loop.

Free gates run before the one paid mapper call; at most one nudge a day; a staged
lever blocks the funnel until EVOLVE/SKIP resolves it; seeds bypass fetch + mapper;
a major lever must survive the rehearsal judge; declines and kills mute the feature.
The model call, Telegram send, propose path, and rehearsal are patched so everything
tests hermetically -- no LLM, no network, no real data files.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import argo_diagnose as dg
import argo_evolve as ev
import argo_incidents as inc
import argo_predictions as pred
import argo_self
import argo_watch
import world_model as wm

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(dt):
    return dt.strftime(_TS_FMT)


class EvolveBase(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.base = base
        self.enterContext(mock.patch.object(ev, "FRONTIER_SEEN_PATH", base / "seen.json"))
        self.enterContext(mock.patch.object(ev, "EVOLUTION_PATH", base / "evo.json"))
        self.enterContext(mock.patch.object(ev, "PENDING_EVOLVE_PATH", base / "pending.json"))
        self.enterContext(mock.patch.object(argo_self, "SELF_PATH", base / "self.json"))
        self.enterContext(mock.patch.object(wm, "WORLD_MODEL_PATH", base / "wm.json"))
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.enterContext(mock.patch.object(dg, "PROPOSALS_PATH", base / "prop.json"))
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH", base / "inc.json"))
        # argo_watch keeps its own namespace; prove isolation by pointing it elsewhere.
        self.enterContext(mock.patch.object(argo_watch, "SEEN_PATH", base / "watch_seen.json"))
        self.sent = []
        self.enterContext(mock.patch.object(ev, "_send", lambda t: self.sent.append(t) or True))

    def _lever(self, **over):
        entry = {
            "id": "EV-900", "created_at": _iso(datetime.now(timezone.utc)),
            "source": "frontier", "source_item": None,
            "feature": "test_feature", "lever": "do the thing",
            "affected_files": ["src/argo_store.py"],
            "expected_benefit": "benefit", "risk": "risk",
            "magnitude": "minor", "status": "new", "muted_until": None,
            "self_belief_id": None, "world_belief_id": None,
            "prediction_id": None, "prediction_spec": None,
            "pr_number": None, "rehearse": None,
        }
        entry.update(over)
        data = ev._load_ledger()
        data["levers"].append(entry)
        ev._save_ledger(data)
        return entry


class FunnelGateTest(EvolveBase):
    def test_pending_slot_blocks_before_anything(self):
        ev._stage("EV-001")
        with mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("pending", res["reason"])

    def test_nudge_budget_blocks_before_model(self):
        ev._record_nudge()
        with mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("budget", res["reason"])

    def test_no_new_items_no_model_call(self):
        with mock.patch.object(ev, "_collect_new", return_value=[]), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("no new frontier items", res["reason"])

    def test_seed_bypasses_fetch_and_mapper(self):
        ev.ensure_seeds()
        with mock.patch.object(ev, "_collect_new",
                               side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertTrue(res["acted"])
        self.assertEqual(res["feature"], "structured_outputs")  # first seed
        self.assertTrue(ev.has_pending())
        lever = ev.get_lever(res["lever"])
        self.assertEqual(lever["status"], "nudged")
        self.assertTrue(lever["self_belief_id"])           # belief seeded at nudge
        self.assertEqual(ev._nudge_budget_left(), 0)       # budget consumed
        self.assertEqual(len(self.sent), 1)
        self.assertIn("EVOLVE", self.sent[0])

    def test_ensure_seeds_is_idempotent(self):
        self.assertEqual(ev.ensure_seeds(), 3)
        self.assertEqual(ev.ensure_seeds(), 0)
        self.assertEqual(len(ev._load_ledger()["levers"]), 3)

    def test_mapper_lever_lands_in_ledger_and_stages(self):
        items = [{"title": "SDK v9 ships X", "summary": "s", "link": "http://a",
                  "source": "sdk", "_iid": "a"},
                 {"title": "other", "summary": "s", "link": "http://b",
                  "source": "sdk", "_iid": "b"}]
        result = {"relevant": True, "feature": "shiny_thing",
                  "lever": "adopt it", "affected_files": ["src/argo_store.py"],
                  "expected_benefit": "cheaper", "risk": "low",
                  "magnitude": "minor", "source_title": "SDK v9 ships X"}
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=result):
            res = ev.scan()
        self.assertTrue(res["acted"])
        lever = ev.get_lever(res["lever"])
        self.assertEqual(lever["source"], "frontier")
        self.assertEqual(lever["feature"], "shiny_thing")
        seen = ev.load_seen()
        self.assertEqual(seen["a"], ev.MAX_ATTEMPTS)  # chosen item settled
        self.assertEqual(seen["b"], 1)                # unused item bumped

    def test_failed_nudge_leaves_lever_retryable(self):
        items = [{"title": "SDK v9 ships X", "summary": "s", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        result = {"relevant": True, "feature": "shiny_thing",
                  "lever": "adopt it", "affected_files": ["src/argo_store.py"],
                  "expected_benefit": "cheaper", "risk": "low",
                  "magnitude": "minor", "source_title": "SDK v9 ships X"}
        with mock.patch.object(ev, "_send", lambda t: False), \
             mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=result):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("nudge delivery failed", res["reason"])
        self.assertFalse(ev.has_pending())
        lever = ev.get_lever(res["lever"])
        self.assertEqual(lever["status"], "nudge-ready")  # retryable, not stranded
        bid = lever["self_belief_id"]
        self.assertTrue(bid)                              # belief persisted pre-send
        # The next scan retries via the seed gate: no fetch, no mapper, no new belief.
        with mock.patch.object(ev, "_collect_new",
                               side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res2 = ev.scan()
        self.assertTrue(res2["acted"])
        self.assertEqual(res2["lever"], res["lever"])
        lever = ev.get_lever(res2["lever"])
        self.assertEqual(lever["status"], "nudged")
        self.assertEqual(lever["self_belief_id"], bid)
        self.assertEqual(len(argo_self.get_self_beliefs(kind="capability")), 1)

    def test_mapper_failure_does_not_penalize_seen(self):
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=None):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertEqual(ev.load_seen(), {})  # untouched on infrastructure failure

    def test_feature_dedup_blocks_in_flight(self):
        self._lever(feature="shiny_thing", status="pr_open", pr_number=5)
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        result = {"relevant": True, "feature": "shiny_thing", "lever": "x",
                  "affected_files": ["src/argo_store.py"], "expected_benefit": "b",
                  "risk": "r", "magnitude": "minor", "source_title": "t"}
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=result):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("already tracked", res["reason"])

    def test_mute_expiry_frees_the_feature(self):
        past = _iso(datetime.now(timezone.utc) - timedelta(days=1))
        self._lever(feature="shiny_thing", status="rejected", muted_until=past)
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        result = {"relevant": True, "feature": "shiny_thing", "lever": "x",
                  "affected_files": ["src/argo_store.py"], "expected_benefit": "b",
                  "risk": "r", "magnitude": "minor", "source_title": "t"}
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=result):
            res = ev.scan()
        self.assertTrue(res["acted"])

    def test_nonexistent_affected_files_refused(self):
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        result = {"relevant": True, "feature": "ghost", "lever": "x",
                  "affected_files": ["src/nope_xyz.py"], "expected_benefit": "b",
                  "risk": "r", "magnitude": "minor", "source_title": "t"}
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value=result):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertIn("affected files", res["reason"])
        # The rejected mapper hit is attempt-bumped, not retired: the item stays
        # eligible for a retry until MAX_ATTEMPTS.
        self.assertEqual(ev.load_seen()["a"], 1)

    def test_malformed_mapper_reply_is_infrastructure_failure(self):
        # Neither NONE nor JSON: must come back None (no attempt penalty), never
        # be coerced to {} as if the mapper had really judged the items.
        import argo_observe
        with mock.patch.object(ev, "_resolve_model", return_value="claude-x"), \
             mock.patch.object(argo_observe, "provider_for",
                               return_value={"name": "anthropic"}), \
             mock.patch.object(argo_observe, "chat_with_mcp",
                               return_value="sure! here is a lever idea: broken"):
            self.assertIsNone(ev._map_levers([{"title": "t"}]))
        with mock.patch.object(ev, "_resolve_model", return_value="claude-x"), \
             mock.patch.object(argo_observe, "provider_for",
                               return_value={"name": "anthropic"}), \
             mock.patch.object(argo_observe, "chat_with_mcp",
                               return_value="NONE"):
            self.assertEqual(ev._map_levers([{"title": "t"}]), {})

    def test_orphaned_nudged_lever_is_rearmed(self):
        # A gate turn cleared the staging slot then died before writing the next
        # status: the nudged lever has no slot, so EVOLVE can never reach it.
        past = _iso(datetime.now(timezone.utc)
                    - timedelta(hours=ev.STALE_CLAIM_HOURS + 1))
        self._lever(id="EV-912", feature="orphan_one", status="nudged",
                    nudged_at=past)
        with mock.patch.object(ev, "_collect_new",
                               side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertTrue(res["acted"])
        self.assertEqual(res["lever"], "EV-912")  # re-armed and re-offered
        self.assertEqual(ev.get_lever("EV-912")["status"], "nudged")
        self.assertTrue(ev.has_pending())

    def test_staged_nudged_lever_waits_indefinitely(self):
        # A properly staged lever may wait days for the user's EVOLVE/SKIP: the
        # sweep must never re-arm it, however old the nudge is.
        past = _iso(datetime.now(timezone.utc) - timedelta(days=10))
        self._lever(id="EV-913", feature="patient_one", status="nudged",
                    nudged_at=past)
        ev._stage("EV-913")
        res = ev.scan()
        self.assertFalse(res["acted"])  # GATE 1: pending blocks the funnel
        self.assertEqual(ev.get_lever("EV-913")["status"], "nudged")

    def test_source_title_match_exact_first_ambiguous_skipped(self):
        items = [{"title": "SDK v9", "_iid": "a"},
                 {"title": "SDK v9.1 hotfix", "_iid": "b"}]
        self.assertEqual(ev._match_item(items, " sdk V9 ")["_iid"], "a")  # exact wins
        self.assertEqual(ev._match_item(items, "hotfix")["_iid"], "b")    # unique substring
        self.assertIsNone(ev._match_item(items, "v9"))                    # ambiguous: no link
        self.assertIsNone(ev._match_item(items, ""))

    def test_rejected_relevance_bumps_not_retires(self):
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers",
                               return_value={"relevant": False, "source_title": "t"}):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertEqual(ev.load_seen()["a"], 1)

    def test_watch_seen_store_is_isolated(self):
        items = [{"title": "t", "summary": "", "link": "http://a",
                  "source": "sdk", "_iid": "a"}]
        with mock.patch.object(ev, "_collect_new", return_value=items), \
             mock.patch.object(ev, "_map_levers", return_value={}):
            ev.scan()
        self.assertTrue((self.base / "seen.json").exists())
        self.assertFalse((self.base / "watch_seen.json").exists())


class GateCommandsTest(EvolveBase):
    def _offer_seed(self):
        ev.ensure_seeds()
        res = ev.scan()
        self.assertTrue(res["acted"])
        return res["lever"]

    def test_decline_mutes_for_a_month(self):
        lid = self._offer_seed()
        text = ev.decline_pending()
        self.assertIn("Dropped", text)
        self.assertFalse(ev.has_pending())
        lever = ev.get_lever(lid)
        self.assertEqual(lever["status"], "rejected")
        mu = datetime.strptime(lever["muted_until"], _TS_FMT)
        days = (mu - datetime.utcnow()).days
        self.assertGreaterEqual(days, ev.MUTE_DAYS_SKIP - 2)
        beliefs = argo_self.get_self_beliefs(kind="capability")
        self.assertEqual(len(beliefs[0].get("refutations", [])), 1)

    def test_decline_with_nothing_staged(self):
        self.assertIn("Nothing staged", ev.decline_pending())

    def test_skip_during_inflight_evolve_does_not_reject(self):
        # accept_pending claims the lever (status evolving, staging cleared) before
        # its slow rehearse/propose work; a SKIP landing then must not fight it.
        self._lever(id="EV-905", feature="busy_one", status="evolving")
        text = ev.decline_pending()
        self.assertIn("mid-evolve", text)
        self.assertIn("busy_one", text)
        self.assertEqual(ev.get_lever("EV-905")["status"], "evolving")

    def test_accept_refuses_lever_not_nudged(self):
        # A second EVOLVE (or one racing a SKIP) finds the lever already claimed
        # or resolved: it must refuse instead of double-proposing.
        self._lever(id="EV-906", feature="gone", status="rejected")
        ev._stage("EV-906")
        with mock.patch.object(ev, "_propose",
                               side_effect=AssertionError("must not propose")), \
             mock.patch.object(ev, "_rehearse_lever",
                               side_effect=AssertionError("must not rehearse")):
            text = ev.accept_pending()
        self.assertIn("already rejected", text)
        self.assertEqual(ev.get_lever("EV-906")["status"], "rejected")

    def test_accept_minor_drafts_pr_and_joins_ledger(self):
        lid = self._offer_seed()  # structured_outputs: minor, has a prediction spec
        captured = {}

        def fake_propose(payload):
            captured.update(payload)
            dg.append_proposal(101, "http://pr/101", payload["belief_id"],
                               payload["incident_key"])
            return ("Drafted a fix and opened http://pr/101 for your review.",
                    {"pr_number": 101, "url": "http://pr/101"})

        with mock.patch.object(ev, "_propose", side_effect=fake_propose), \
             mock.patch.object(ev, "_rehearse_lever",
                               side_effect=AssertionError("minor must not rehearse")):
            text = ev.accept_pending()
        self.assertIn("http://pr/101", text)
        self.assertFalse(ev.has_pending())
        lever = ev.get_lever(lid)
        self.assertEqual(lever["status"], "pr_open")
        self.assertEqual(lever["pr_number"], 101)
        # The payload reuses the diagnose shape (belief joins verify/confirm).
        self.assertEqual(captured["belief_id"], lever["self_belief_id"])
        self.assertIsNone(captured["incident_key"])
        self.assertEqual(captured["suspected_files"], lever["affected_files"])
        # World-model belief + UNARMED prediction recorded at accept time.
        self.assertTrue(lever["world_belief_id"])
        p = pred.get_prediction(lever["prediction_id"])
        self.assertIsNotNone(p)
        self.assertIsNone(p["armed_at"])
        self.assertEqual(p["belief_id"], lever["world_belief_id"])

    def test_accept_major_kill_is_terminal(self):
        self._lever(id="EV-901", feature="big_bet", magnitude="major", status="nudged")
        ev._stage("EV-901")
        with mock.patch.object(ev, "_rehearse_lever",
                               return_value=("KILL", "VERDICT: KILL - too risky")), \
             mock.patch.object(ev, "_propose",
                               side_effect=AssertionError("killed lever must not propose")):
            text = ev.accept_pending()
        self.assertIn("judge said no", text)
        lever = ev.get_lever("EV-901")
        self.assertEqual(lever["status"], "killed")
        self.assertIsNotNone(lever["muted_until"])
        self.assertEqual(lever["rehearse"]["verdict"], "KILL")

    def test_accept_major_rehearse_failure_keeps_it_staged(self):
        self._lever(id="EV-902", feature="big_bet2", magnitude="major", status="nudged")
        ev._stage("EV-902")
        with mock.patch.object(ev, "_rehearse_lever", return_value=(None, "no model")), \
             mock.patch.object(ev, "_propose",
                               side_effect=AssertionError("must not propose")):
            text = ev.accept_pending()
        self.assertIn("still staged", text)
        self.assertTrue(ev.has_pending())  # retryable
        # The claim is released too, so the retry EVOLVE passes the status check.
        self.assertEqual(ev.get_lever("EV-902")["status"], "nudged")
        with mock.patch.object(ev, "_rehearse_lever", return_value=("PROCEED", "ok")), \
             mock.patch.object(ev, "_propose",
                               return_value=("Drafted a fix and opened http://pr/303.",
                                             {"pr_number": 303, "url": "http://pr/303"})):
            ev.accept_pending()
        self.assertEqual(ev.get_lever("EV-902")["status"], "pr_open")

    def test_accept_propose_failure_rests_a_week(self):
        self._lever(id="EV-903", feature="meh", magnitude="minor", status="nudged")
        ev._stage("EV-903")
        with mock.patch.object(ev, "_propose",
                               return_value=("I couldn't draft a fix I trust for that one.",
                                             None)):
            text = ev.accept_pending()
        self.assertIn("couldn't draft", text)
        lever = ev.get_lever("EV-903")
        self.assertEqual(lever["status"], "failed")
        self.assertIsNotNone(lever["muted_until"])

    def test_accept_tracks_pr_even_if_ledger_append_failed(self):
        self._lever(id="EV-904", feature="lucky", magnitude="minor", status="nudged")
        ev._stage("EV-904")
        # _propose opened a real PR but the proposals-ledger write failed: the lever
        # must still go pr_open with the returned number, never "failed".
        with mock.patch.object(ev, "_propose",
                               return_value=("Drafted a fix and opened http://pr/202.",
                                             {"pr_number": 202, "url": "http://pr/202"})):
            text = ev.accept_pending()
        self.assertIn("http://pr/202", text)
        lever = ev.get_lever("EV-904")
        self.assertEqual(lever["status"], "pr_open")
        self.assertEqual(lever["pr_number"], 202)
        # And the missing ledger row is re-recorded so sync can follow the PR.
        rows = dg._load_proposals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pr_number"], 202)
        self.assertEqual(rows[0]["belief_id"], lever["self_belief_id"])

    def test_accept_with_nothing_staged(self):
        self.assertIn("Nothing staged", ev.accept_pending())

    def test_stale_claim_is_rearmed_and_reoffered(self):
        # A crash mid-accept left this lever claimed with no PR: the next scan
        # must re-arm it and offer it again instead of leaving it stuck forever.
        past = _iso(datetime.now(timezone.utc) - timedelta(hours=ev.STALE_CLAIM_HOURS + 1))
        self._lever(id="EV-907", feature="stuck_one", status="evolving",
                    claimed_at=past)
        with mock.patch.object(ev, "_collect_new",
                               side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertTrue(res["acted"])
        self.assertEqual(res["lever"], "EV-907")
        self.assertEqual(ev.get_lever("EV-907")["status"], "nudged")

    def test_fresh_claim_is_left_alone(self):
        self._lever(id="EV-908", feature="busy_now", status="evolving",
                    claimed_at=_iso(datetime.now(timezone.utc)))
        with mock.patch.object(ev, "_collect_new", return_value=[]):
            res = ev.scan()
        self.assertFalse(res["acted"])
        self.assertEqual(ev.get_lever("EV-908")["status"], "evolving")

    def test_active_claim_shields_stale_sweep(self):
        # A live accept thread may legitimately outlast the lease (slow rehearse/
        # propose): its registered claim must never be swept out from under it.
        past = _iso(datetime.now(timezone.utc)
                    - timedelta(hours=ev.STALE_CLAIM_HOURS + 1))
        self._lever(id="EV-909", feature="slow_one", status="evolving",
                    claimed_at=past)
        ev._ACTIVE_CLAIMS.add("EV-909")
        try:
            with mock.patch.object(ev, "_collect_new", return_value=[]):
                ev.scan()
            self.assertEqual(ev.get_lever("EV-909")["status"], "evolving")
        finally:
            ev._ACTIVE_CLAIMS.discard("EV-909")

    def test_accept_registers_active_claim_for_duration(self):
        self._lever(id="EV-911", feature="registered", magnitude="major",
                    status="nudged")
        ev._stage("EV-911")
        membership = []

        def fake_rehearse(lever):
            membership.append(lever["id"] in ev._ACTIVE_CLAIMS)
            return (None, "no model")

        with mock.patch.object(ev, "_rehearse_lever", side_effect=fake_rehearse):
            ev.accept_pending()
        self.assertEqual(membership, [True])      # shielded while working
        self.assertNotIn("EV-911", ev._ACTIVE_CLAIMS)  # released after


class SyncOutcomesTest(EvolveBase):
    def _adopted_lever(self):
        bid = argo_self.add_self_belief("adopting x improves me", kind="capability",
                                        source="evolution")
        wm_id = wm.add_belief("Adopting x improves Argo", source_finding="evolution:EV-910")
        pid = pred.record(wm_id, "no recurrence in 14 days",
                          {"kind": "incident_absent", "key": "tool_error|x"}, 14,
                          source="evolution:EV-910")
        self._lever(id="EV-910", feature="x_feature", status="pr_open", pr_number=7,
                    self_belief_id=bid, world_belief_id=wm_id, prediction_id=pid)
        dg.append_proposal(7, "http://pr/7", bid, None)
        return bid, wm_id, pid

    def _set_proposal(self, **fields):
        items = dg._load_proposals()
        items[0].update(fields)
        dg._save_proposals(items)

    def test_merge_arms_prediction_and_watches(self):
        _, _, pid = self._adopted_lever()
        self._set_proposal(merged=True, merged_at="2026-06-10T00:00:00Z")
        ev.sync_proposal_outcomes()
        self.assertEqual(ev.get_lever("EV-910")["status"], "merged_watch")
        p = pred.get_prediction(pid)
        self.assertEqual(p["armed_at"], "2026-06-10T00:00:00Z")
        self.assertEqual(p["due"], "2026-06-24T00:00:00Z")

    def test_resolved_and_held_confirms_with_wm_evidence(self):
        bid, wm_id, _ = self._adopted_lever()
        self._set_proposal(merged=True, merged_at="2026-06-10T00:00:00Z")
        ev.sync_proposal_outcomes()
        argo_self.resolve_self_belief(bid, "PR #7 merged, no recurrence")
        self._set_proposal(resolved=True)
        ev.sync_proposal_outcomes()
        self.assertEqual(ev.get_lever("EV-910")["status"], "confirmed")
        belief = wm.get_belief(wm_id)
        self.assertTrue(any("quiet" in e for e in belief["evidence"]))

    def test_held_stamp_confirms_without_belief_join(self):
        # confirm_deployed stamps the verdict on the proposal row; sync must trust
        # it even when the belief-id plumbing diverges (belief never resolved here).
        _, wm_id, _ = self._adopted_lever()
        self._set_proposal(merged=True, merged_at="2026-06-10T00:00:00Z",
                           resolved=True, held=True)
        ev.sync_proposal_outcomes()
        self.assertEqual(ev.get_lever("EV-910")["status"], "confirmed")
        self.assertTrue(wm.get_belief(wm_id)["evidence"])

    def test_held_false_fails_the_lever(self):
        bid, wm_id, _ = self._adopted_lever()
        argo_self.resolve_self_belief(bid, "stale resolution")  # stamp must win
        self._set_proposal(merged=True, merged_at="2026-06-10T00:00:00Z",
                           resolved=True, held=False)
        ev.sync_proposal_outcomes()
        lever = ev.get_lever("EV-910")
        self.assertEqual(lever["status"], "failed")
        self.assertTrue(wm.get_belief(wm_id)["refutations"])

    def test_ci_failure_fails_the_lever_and_refutes(self):
        _, wm_id, _ = self._adopted_lever()
        self._set_proposal(ci_failed=True)
        ev.sync_proposal_outcomes()
        lever = ev.get_lever("EV-910")
        self.assertEqual(lever["status"], "failed")
        self.assertIsNotNone(lever["muted_until"])
        self.assertTrue(wm.get_belief(wm_id)["refutations"])


    def test_wrong_prediction_fails_confirmed_lever(self):
        # The dated prediction is the final grader: a wrong score must close the
        # lever lifecycle, not leave 'confirmed' blocking the slug forever.
        self._adopted_lever()
        ev._update_lever("EV-910", status="confirmed")
        items = pred._load()
        items[0].update(scored_at="2026-06-25T00:00:00Z", correct=False)
        pred._save(items)
        ev._apply_prediction_verdicts()
        lever = ev.get_lever("EV-910")
        self.assertEqual(lever["status"], "failed")
        self.assertIsNotNone(lever["muted_until"])

    def test_correct_or_unscored_prediction_keeps_confirmed(self):
        self._adopted_lever()
        ev._update_lever("EV-910", status="confirmed")
        ev._apply_prediction_verdicts()  # unscored: untouched
        self.assertEqual(ev.get_lever("EV-910")["status"], "confirmed")
        items = pred._load()
        items[0].update(scored_at="2026-06-25T00:00:00Z", correct=True)
        pred._save(items)
        ev._apply_prediction_verdicts()
        self.assertEqual(ev.get_lever("EV-910")["status"], "confirmed")


class PlacementGuardTest(EvolveBase):
    def test_run_cli_is_inert_on_actions_without_volume(self):
        env = {k: v for k, v in os.environ.items() if k != "ARGO_EVOLUTION_PATH"}
        env["GITHUB_ACTIONS"] = "true"
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ev, "ensure_seeds",
                               side_effect=AssertionError("must not run on Actions")):
            res = ev.run_cli()
        self.assertFalse(res["acted"])
        self.assertEqual(res["reason"], "actions-no-volume")

    def test_run_gaps_cli_is_inert_on_actions_without_volume(self):
        env = {k: v for k, v in os.environ.items() if k != "ARGO_EVOLUTION_PATH"}
        env["GITHUB_ACTIONS"] = "true"
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ev, "scan_gaps",
                               side_effect=AssertionError("must not run on Actions")):
            res = ev.run_gaps_cli()
        self.assertFalse(res["acted"])
        self.assertEqual(res["reason"], "actions-no-volume")


class GapProposerTest(EvolveBase):
    """The inward twin of the frontier funnel (scan_gaps): same gates, ledger, and
    EVOLVE/SKIP gate as scan(), but the signal is Argo's OWN gaps. The gap mapper,
    its collect step, and the Telegram send are patched so it tests hermetically --
    no LLM, no network, no real data files."""

    _RESULT = {"relevant": True, "gap": "no usage/cost telemetry",
               "feature": "usage_cost_telemetry", "lever": "record per-call tokens",
               "affected_files": ["src/argo_store.py"],
               "expected_benefit": "cost claims become scorable",
               "risk": "a write on every call path", "magnitude": "minor"}

    def test_pending_slot_blocks_before_gap_mapper(self):
        ev._stage("EV-001")
        with mock.patch.object(ev, "_map_gap",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("pending", res["reason"])

    def test_nudge_budget_blocks_before_gap_mapper(self):
        # Shared budget: a frontier nudge already spent today blocks the gap funnel.
        ev._record_nudge()
        with mock.patch.object(ev, "_map_gap",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("budget", res["reason"])

    def test_no_gaps_no_model_call(self):
        with mock.patch.object(ev, "_collect_gaps", return_value=[]), \
             mock.patch.object(ev, "_map_gap",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("no open capability gaps", res["reason"])

    def test_gap_lever_lands_with_source_gap_and_stages(self):
        with mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=self._RESULT):
            res = ev.scan_gaps()
        self.assertTrue(res["acted"])
        lever = ev.get_lever(res["lever"])
        self.assertEqual(lever["source"], "gap")
        self.assertEqual(lever["feature"], "usage_cost_telemetry")
        self.assertEqual(lever["status"], "nudged")
        self.assertIsNone(lever["prediction_spec"])    # gap levers are unscored
        self.assertTrue(lever["self_belief_id"])        # belief seeded at nudge
        self.assertTrue(ev.has_pending())
        self.assertEqual(ev._nudge_budget_left(), 0)     # shared budget consumed
        self.assertEqual(len(self.sent), 1)
        self.assertIn("gap in myself", self.sent[0])     # inward framing, not frontier
        self.assertIn("EVOLVE", self.sent[0])

    def test_gap_feature_dedup_against_active_features(self):
        # A feature already in flight (here a frontier lever) must not be re-proposed
        # by the inward funnel: the two funnels share one ledger and feature space.
        self._lever(feature="usage_cost_telemetry", status="pr_open", pr_number=9)
        with mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=self._RESULT):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("already tracked", res["reason"])

    def test_nonexistent_affected_files_refused(self):
        bad = dict(self._RESULT, affected_files=["src/nope_xyz.py"])
        with mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=bad):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("affected files", res["reason"])

    def test_undelivered_gap_lever_reoffered_without_model(self):
        # A failed nudge send leaves the gap lever nudge-ready; the next scan must
        # re-offer it via GATE 3 without burning another model call.
        with mock.patch.object(ev, "_send", lambda t: False), \
             mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=self._RESULT):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("nudge delivery failed", res["reason"])
        lever = ev.get_lever(res["lever"])
        self.assertEqual(lever["status"], "nudge-ready")
        self.assertEqual(lever["source"], "gap")
        bid = lever["self_belief_id"]
        self.assertTrue(bid)                              # belief persisted pre-send
        with mock.patch.object(ev, "_collect_gaps",
                               side_effect=AssertionError("collect must not run")), \
             mock.patch.object(ev, "_map_gap",
                               side_effect=AssertionError("mapper must not run")):
            res2 = ev.scan_gaps()
        self.assertTrue(res2["acted"])
        self.assertEqual(res2["lever"], res["lever"])     # same lever, re-offered
        self.assertEqual(ev.get_lever(res2["lever"])["status"], "nudged")
        self.assertEqual(ev.get_lever(res2["lever"])["self_belief_id"], bid)

    def test_gap_mapper_none_means_nothing_worth_closing(self):
        with mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value={}):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("no gap worth closing", res["reason"])

    def test_gap_mapper_infrastructure_failure(self):
        with mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=None):
            res = ev.scan_gaps()
        self.assertFalse(res["acted"])
        self.assertIn("gap mapper unavailable", res["reason"])

    def test_collect_gaps_reads_manifest_and_self_issues(self):
        # The pure read step: manifest 'not used' entries plus unresolved self
        # 'issue' beliefs become the candidate list; resolved issues drop out.
        man = self.base / "manifest.json"
        man.write_text('{"api_features_not_used": ["telemetry gap"]}')
        argo_self.add_self_belief("I keep mis-parsing JSON", kind="issue",
                                  source="test")
        with mock.patch.object(ev, "MANIFEST_PATH", man):
            gaps = ev._collect_gaps()
        self.assertTrue(any("telemetry gap" in g for g in gaps))
        self.assertTrue(any("mis-parsing JSON" in g for g in gaps))

    def test_gaps_no_send_is_a_real_dry_run(self):
        # `--gaps --no-send` must collect + map + print only -- no send, no staging,
        # no ledger write -- like the frontier dry run (Bugbot finding, PR #21).
        result = {"relevant": True, "feature": "f", "lever": "l",
                  "affected_files": ["src/argo_store.py"], "expected_benefit": "b",
                  "risk": "r", "magnitude": "minor", "gap": "g"}
        with mock.patch.object(ev.sys, "argv", ["argo_evolve.py", "--gaps", "--no-send"]), \
             mock.patch.object(ev, "_collect_gaps", return_value=["stack gap: x"]), \
             mock.patch.object(ev, "_map_gap", return_value=result), \
             mock.patch.object(ev, "_offer",
                               side_effect=AssertionError("dry run must not offer")):
            ev.main()
        self.assertEqual(self.sent, [])                     # nothing sent
        self.assertFalse(ev.has_pending())                  # nothing staged
        self.assertEqual(ev._load_ledger()["levers"], [])   # no ledger write

    def test_frontier_gate3_prefers_stranded_gap_lever_over_seed(self):
        # A gap lever whose send failed sits nudge-ready alongside the dogfood seeds;
        # the daily frontier run's GATE 3 must re-offer the GAP lever (honor the slot
        # gaps already tried to use), not the first seed in list order (Bugbot, #21).
        ev.ensure_seeds()  # 3 nudge-ready seeds, source="seed", added first
        self._lever(id="EV-950", feature="gap_feat", source="gap",
                    status="nudge-ready", source_item={"title": "a gap"})
        with mock.patch.object(ev, "_collect_new",
                               side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(ev, "_map_levers",
                               side_effect=AssertionError("mapper must not run")):
            res = ev.scan()
        self.assertTrue(res["acted"])
        self.assertEqual(res["lever"], "EV-950")            # gap lever wins, not a seed
        self.assertEqual(ev.get_lever("EV-950")["status"], "nudged")
        self.assertIn("gap in myself", self.sent[-1])       # inward voice preserved

    def test_accept_gap_lever_labels_pr_as_capability_gap(self):
        # A gap-sourced PR body must read as a capability-gap upgrade, not an external
        # "Frontier upgrade" (the nudge already uses gap copy) -- Bugbot, PR #21.
        self._lever(id="EV-960", feature="gap_pr", source="gap", magnitude="minor",
                    status="nudged")
        ev._stage("EV-960")
        captured = {}

        def fake_propose(payload):
            captured.update(payload)
            return ("Drafted a fix and opened http://pr/960.",
                    {"pr_number": 960, "url": "http://pr/960"})

        with mock.patch.object(ev, "_propose", side_effect=fake_propose):
            ev.accept_pending()
        self.assertIn("Capability-gap upgrade", captured["description"])
        self.assertNotIn("Frontier upgrade", captured["description"])


if __name__ == "__main__":
    unittest.main()
