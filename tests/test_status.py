"""Ambient status (H3.3) + who-acts-next classifier.

Covers argo_status: the read-only "what's in flight / needs attention" query and
its needs-you / agent-can-act / blocked classifier, plus the plain-text render.

Three things the feature must get right:
  1. each in-flight item lands in the right verdict bucket (the classifier rules);
  2. the rendered status is plain text (no markdown, no em dashes) and lists the
     in-flight items;
  3. empty stores render a graceful "nothing in flight" message, never a crash.

Pure + hermetic: synthetic stores in a tmp dir, every path constant overridden,
no network / no LLM / no real data/*.json. argo_status reads the four staged
stores via argo_paths.<CONST> at call time, so those are patched on argo_paths;
predictions / world model / evolution are read via their own module constants, so
those are patched there.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import argo_paths
import argo_store
import argo_status as status
import argo_predictions as pred
import argo_evolve as ev
import world_model as wm

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ts(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime(_FMT)


class StatusBase(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.base = base
        # The four staged stores: argo_status reads argo_paths.<CONST> live.
        for name in ("PROPOSALS_PATH", "PENDING_EVOLVE_PATH", "PENDING_HEAL_PATH",
                     "PENDING_DECISIONS_PATH"):
            self.enterContext(mock.patch.object(argo_paths, name,
                                                base / f"{name.lower()}.json"))
        # Predictions / world model / evolution read their own module constants.
        self.enterContext(mock.patch.object(pred, "PREDICTIONS_PATH", base / "pred.json"))
        self.enterContext(mock.patch.object(wm, "WORLD_MODEL_PATH", base / "wm.json"))
        self.enterContext(mock.patch.object(ev, "EVOLUTION_PATH", base / "evo.json"))

    def _write(self, const_module, attr, data):
        argo_store.save_json(getattr(const_module, attr), data)


class ClassifierTest(StatusBase):
    def test_needs_you_items(self):
        # A staged EVOLVE/SKIP upgrade, a staged FIX self-fix, an open PR awaiting
        # merge, and an open owner-question are all blocked on the human.
        self._write(argo_paths, "PENDING_EVOLVE_PATH", {"lever_id": "EV-007"})
        self._write(argo_paths, "PENDING_HEAL_PATH",
                    {"action": "propose_fix", "payload": {"title": "fix the breaker"}})
        self._write(argo_paths, "PROPOSALS_PATH",
                    [{"pr_number": 12, "incident_key": "model_failure",
                      "merged": False, "ci_failed": False, "resolved": False}])
        self._write(argo_paths, "PENDING_DECISIONS_PATH",
                    [{"id": "D-003", "status": "open", "question": "ship it?"}])

        out = status.collect()
        by_id = {it["id"]: it["verdict"] for it in out["items"]}
        self.assertEqual(by_id["EV-007"], status.NEEDS_YOU)
        self.assertEqual(by_id["heal"], status.NEEDS_YOU)
        self.assertEqual(by_id["PR-12"], status.NEEDS_YOU)
        self.assertEqual(by_id["D-003"], status.NEEDS_YOU)

    def test_agent_can_act_items(self):
        # A due armed prediction, a lever mid-rehearse, a merged-not-resolved fix:
        # Argo's own loops advance these, no human needed.
        self._write(pred, "PREDICTIONS_PATH",
                    [{"id": "EVP-001", "claim": "P-001 ships", "armed_at": _ts(-14),
                      "due": _ts(-1), "scored_at": None}])
        self._write(ev, "EVOLUTION_PATH",
                    {"_meta": {}, "levers": [
                        {"id": "EV-001", "feature": "caching", "status": "evolving"}]})
        self._write(argo_paths, "PROPOSALS_PATH",
                    [{"pr_number": 9, "incident_key": "tool_error",
                      "merged": True, "ci_failed": False, "resolved": False}])

        out = status.collect()
        by_id = {it["id"]: it["verdict"] for it in out["items"]}
        self.assertEqual(by_id["EVP-001"], status.AGENT_CAN_ACT)
        self.assertEqual(by_id["EV-001"], status.AGENT_CAN_ACT)
        self.assertEqual(by_id["PR-9"], status.AGENT_CAN_ACT)

    def test_blocked_items(self):
        # An unarmed prediction (clock can't start), a failed evolution lever, and a
        # CI-failed fix PR cannot advance without an external fix.
        self._write(pred, "PREDICTIONS_PATH",
                    [{"id": "EVP-005", "claim": "waiting on merge", "armed_at": None,
                      "due": None, "scored_at": None}])
        self._write(ev, "EVOLUTION_PATH",
                    {"_meta": {}, "levers": [
                        {"id": "EV-002", "feature": "batch", "status": "failed"}]})
        self._write(argo_paths, "PROPOSALS_PATH",
                    [{"pr_number": 4, "incident_key": "phantom_send",
                      "merged": False, "ci_failed": True, "resolved": False}])

        out = status.collect()
        by_id = {it["id"]: it["verdict"] for it in out["items"]}
        self.assertEqual(by_id["EVP-005"], status.BLOCKED)
        self.assertEqual(by_id["EV-002"], status.BLOCKED)
        self.assertEqual(by_id["PR-4"], status.BLOCKED)

    def test_settled_and_terminal_items_excluded(self):
        # A scored/voided prediction, a resolved PR, and a terminal (rejected) lever
        # are not in flight and must not appear.
        self._write(pred, "PREDICTIONS_PATH",
                    [{"id": "EVP-010", "claim": "done", "armed_at": _ts(-30),
                      "due": _ts(-16), "scored_at": _ts(-1), "correct": True},
                     {"id": "EVP-011", "claim": "voided", "armed_at": _ts(-5),
                      "due": _ts(9), "scored_at": _ts(-1), "voided": True}])
        self._write(argo_paths, "PROPOSALS_PATH",
                    [{"pr_number": 1, "incident_key": "x", "resolved": True}])
        self._write(ev, "EVOLUTION_PATH",
                    {"_meta": {}, "levers": [
                        {"id": "EV-099", "feature": "old", "status": "rejected"}]})

        ids = {it["id"] for it in status.collect()["items"]}
        self.assertEqual(ids, set())


class RenderTest(StatusBase):
    def test_render_is_plain_text_and_lists_items(self):
        self._write(argo_paths, "PENDING_EVOLVE_PATH", {"lever_id": "EV-007"})
        self._write(pred, "PREDICTIONS_PATH",
                    [{"id": "EVP-001", "claim": "P-001 ships", "armed_at": _ts(-14),
                      "due": _ts(-1), "scored_at": None}])
        self._write(argo_paths, "PROPOSALS_PATH",
                    [{"pr_number": 4, "incident_key": "phantom_send",
                      "merged": False, "ci_failed": True, "resolved": False}])

        text = status.render()
        # Lists the in-flight items under their verdict groups.
        self.assertIn("EV-007", text)
        self.assertIn("EVP-001", text)
        self.assertIn("PR #4", text)
        self.assertIn("Needs you", text)
        self.assertIn("Blocked", text)
        # Plain text: no markdown bold/headers/bullets-as-asterisks, no em/en dash.
        self.assertNotIn("**", text)
        self.assertNotIn("##", text)
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)
        # Plain by construction: passing it through the webhook's _clean_reply
        # (the gate wraps it) must not change it -- no markdown left to strip.
        import argo_webhook as wh
        self.assertEqual(wh._clean_reply(text), text)

    def test_belief_moves_surface_as_context(self):
        self._write(wm, "WORLD_MODEL_PATH",
                    [{"id": "WM-001", "claim": "judgment compounds", "confidence": 0.6,
                      "status": "active", "last_updated": "2026-06-19"}])
        text = status.render()
        self.assertIn("Recent belief moves", text)
        self.assertIn("WM-001", text)


class EmptyStoresTest(StatusBase):
    def test_all_empty_is_graceful(self):
        # No stores written at all: collect() returns nothing, render() is graceful.
        out = status.collect()
        self.assertEqual(out["items"], [])
        self.assertEqual(out["beliefs"], [])
        text = status.render()
        self.assertIn("Nothing in flight", text)
        self.assertNotIn("**", text)
        self.assertNotIn("—", text)

    def test_malformed_stores_do_not_crash(self):
        # A store holding the wrong JSON type must not raise -- it yields no items.
        argo_store.save_json(argo_paths.PROPOSALS_PATH, {"not": "a list"})
        argo_store.save_json(pred.PREDICTIONS_PATH, {"not": "a list"})
        text = status.render()  # must not raise
        self.assertIn("Nothing in flight", text)


if __name__ == "__main__":
    unittest.main()
