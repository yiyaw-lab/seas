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

    def test_accept_minor_drafts_pr_and_joins_ledger(self):
        lid = self._offer_seed()  # structured_outputs: minor, has a prediction spec
        captured = {}

        def fake_propose(payload):
            captured.update(payload)
            dg.append_proposal(101, "http://pr/101", payload["belief_id"],
                               payload["incident_key"])
            return "Drafted a fix and opened http://pr/101 for your review."

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
        self._lever(id="EV-901", feature="big_bet", magnitude="major")
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
        self._lever(id="EV-902", feature="big_bet2", magnitude="major")
        ev._stage("EV-902")
        with mock.patch.object(ev, "_rehearse_lever", return_value=(None, "no model")), \
             mock.patch.object(ev, "_propose",
                               side_effect=AssertionError("must not propose")):
            text = ev.accept_pending()
        self.assertIn("still staged", text)
        self.assertTrue(ev.has_pending())  # retryable

    def test_accept_propose_failure_rests_a_week(self):
        self._lever(id="EV-903", feature="meh", magnitude="minor")
        ev._stage("EV-903")
        with mock.patch.object(ev, "_propose",
                               return_value="I couldn't draft a fix I trust for that one."):
            text = ev.accept_pending()
        self.assertIn("couldn't draft", text)
        lever = ev.get_lever("EV-903")
        self.assertEqual(lever["status"], "failed")
        self.assertIsNotNone(lever["muted_until"])

    def test_accept_with_nothing_staged(self):
        self.assertIn("Nothing staged", ev.accept_pending())


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
        self.assertTrue(any("held" in e for e in belief["evidence"]))

    def test_ci_failure_fails_the_lever_and_refutes(self):
        _, wm_id, _ = self._adopted_lever()
        self._set_proposal(ci_failed=True)
        ev.sync_proposal_outcomes()
        lever = ev.get_lever("EV-910")
        self.assertEqual(lever["status"], "failed")
        self.assertIsNotNone(lever["muted_until"])
        self.assertTrue(wm.get_belief(wm_id)["refutations"])


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


if __name__ == "__main__":
    unittest.main()
