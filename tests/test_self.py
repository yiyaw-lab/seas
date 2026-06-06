"""Self-model tests (argo_self): capability inventory, self-belief store, reflection.

Pure -- no network, no LLM, no real data/*.json. The MCP registry and the reflection
model call are stubbed; path constants are patched to a temp dir. Mirrors the
tests/test_scheduler.py idiom (enterContext + patch the module-level path constants).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import argo_self as self_mod


class CapabilityInventoryTest(unittest.TestCase):
    def _patch_registry(self, tools):
        import argo_mcp_server
        fake = SimpleNamespace(
            _tool_manager=SimpleNamespace(list_tools=lambda: tools))
        return mock.patch.object(argo_mcp_server, "mcp", fake)

    def test_reads_registry_first_sentence_sorted_clean(self):
        tools = [
            SimpleNamespace(name="beta_tool",
                            description="Does the beta thing. Extra detail ignored."),
            SimpleNamespace(name="alpha_tool",
                            description="Alpha summary — has an em dash. More text."),
        ]
        with self._patch_registry(tools):
            caps = self_mod.list_capabilities()
            block = self_mod.format_capabilities_for_prompt()

        self.assertEqual([c["name"] for c in caps], ["alpha_tool", "beta_tool"])
        # first sentence only
        self.assertEqual(caps[1]["summary"], "Does the beta thing.")
        # plain text: no em/en dashes, no markdown bold
        self.assertNotIn("—", block)
        self.assertNotIn("–", block)
        self.assertNotIn("**", block)
        self.assertIn("alpha_tool", block)
        self.assertIn("beta_tool", block)
        # the instruction that makes Argo recite its REAL tools
        self.assertIn("COMPLETE tool list", block)

    def test_empty_on_registry_error(self):
        bad = [SimpleNamespace(name="x", description="y")]
        with self._patch_registry(bad):
            # break list_tools by pointing the manager at a raiser
            import argo_mcp_server
            argo_mcp_server.mcp._tool_manager.list_tools = (
                lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertEqual(self_mod.list_capabilities(), [])
            self.assertEqual(self_mod.format_capabilities_for_prompt(), "")


class _TmpStoreTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.self_path = base / "argo_self.json"
        self.projects = base / "argo_projects.json"
        self.seen = base / "argo_seen.json"
        self.enterContext(mock.patch.object(self_mod, "SELF_PATH", self.self_path))
        self.enterContext(mock.patch.object(self_mod, "PROJECTS_LOG", self.projects))
        self.enterContext(mock.patch.object(self_mod, "SEEN_PATH", self.seen))

    def _write_projects(self, rows):
        self.projects.write_text(json.dumps(rows))

    def _write_seen(self, mapping):
        self.seen.write_text(json.dumps(mapping))


class SelfBeliefStoreTest(_TmpStoreTest):
    def test_add_is_seeded_and_idempotent(self):
        bid = self_mod.add_self_belief("I am slow on JS-heavy pages", kind="trait")
        self.assertEqual(bid, "SB-001")
        b = self_mod.get_self_beliefs()[0]
        self.assertEqual(b["confidence"], self_mod.SEED_CONFIDENCE)
        self.assertEqual(b["status"], "unverified")
        # identical claim -> same id, no duplicate
        self.assertEqual(
            self_mod.add_self_belief("I am slow on JS-heavy pages"), "SB-001")
        self.assertEqual(len(self_mod.get_self_beliefs()), 1)

    def test_unknown_kind_normalizes_to_lesson(self):
        bid = self_mod.add_self_belief("x", kind="bogus")
        self.assertEqual(self_mod.get_self_beliefs()[0]["kind"], "lesson")
        self.assertTrue(bid)

    def test_evidence_moves_confidence_and_clamps(self):
        bid = self_mod.add_self_belief("claim")
        b = self_mod.add_evidence(bid, "ref-1", supports=True)
        self.assertAlmostEqual(
            b["confidence"], self_mod.SEED_CONFIDENCE + self_mod.EVIDENCE_STEP)
        for i in range(20):  # drive well past the ceiling
            self_mod.add_evidence(bid, f"r{i}", supports=True)
        self.assertEqual(self_mod.get_self_beliefs()[0]["confidence"],
                         self_mod.CONF_MAX)

    def test_refutation_weakens_at_floor(self):
        bid = self_mod.add_self_belief("claim")
        for i in range(20):
            self_mod.add_evidence(bid, f"r{i}", supports=False)
        b = self_mod.get_self_beliefs()[0]
        self.assertEqual(b["confidence"], self_mod.CONF_MIN)
        self.assertEqual(b["status"], "weakening")

    def test_no_set_confidence(self):
        # The inviolable rule, mirrored from world_model: confidence is earned via
        # evidence, never asserted. There must be no set_confidence to launder it.
        self.assertFalse(hasattr(self_mod, "set_confidence"))

    def test_resolve_flips_status_and_records_fix(self):
        bid = self_mod.add_self_belief("tripwire 400s on temperature", kind="issue")
        b = self_mod.resolve_self_belief(bid, "src/argo_observe.py")
        self.assertEqual(b["status"], "resolved")
        self.assertIn("src/argo_observe.py", b["evidence"])

    def test_on_disk_format_round_trips(self):
        self_mod.add_self_belief("claim")
        text = self.self_path.read_text()
        self.assertTrue(text.endswith("\n"))          # argo_store format
        self.assertIsInstance(json.loads(text), list)


class PerformanceTest(_TmpStoreTest):
    def test_means_and_trend(self):
        self._write_projects([
            {"id": "P-001", "energy": 3, "date": "2026-06-01"},
            {"id": "P-002", "energy": 4, "date": "2026-06-02"},
            {"id": "P-003", "energy": 5, "date": "2026-06-03"},
            {"id": "P-004", "energy": 8, "date": "2026-06-04"},
            {"id": "P-005", "energy": 9, "date": "2026-06-05"},
            {"id": "P-006", "energy": 10, "date": "2026-06-06"},
            {"id": "P-007", "date": "2026-06-07"},  # unrated -> excluded
        ])
        self._write_seen({"a": 3, "b": 1, "c": 3})
        s = self_mod.gather_performance()
        self.assertEqual(s["projects_total"], 7)
        self.assertEqual(s["projects_rated"], 6)
        self.assertEqual(s["mean_energy"], 6.5)
        self.assertEqual(s["recent_mean_energy"], 7.2)   # last 5: 4,5,8,9,10
        self.assertEqual(s["energy_trend"], 4.2)         # 7.2 - 3.0
        self.assertEqual(s["tripwire_seen"], 3)
        self.assertEqual(s["tripwire_settled"], 2)       # values >= 3

    def test_graceful_on_missing_files(self):
        s = self_mod.gather_performance()
        self.assertEqual(s["projects_total"], 0)
        self.assertIsNone(s["mean_energy"])
        self.assertEqual(s["tripwire_seen"], 0)


class ReflectionTest(_TmpStoreTest):
    def test_skips_model_call_when_nothing_new(self):
        self._write_projects([{"id": "P-001", "energy": 5, "date": "2026-06-01"}])
        spy = mock.MagicMock(return_value=["should not be used"])
        with mock.patch.object(self_mod, "_reflect_lessons", spy):
            r = self_mod.reflect(force=False)
        self.assertTrue(r["skipped"])
        spy.assert_not_called()
        self.assertEqual(self_mod.get_self_beliefs(), [])

    def test_records_lessons_advances_marker_idempotent(self):
        self._write_projects([
            {"id": f"P-00{i}", "energy": 5, "date": f"2026-06-0{i}"}
            for i in range(1, 4)
        ])
        lessons = ["Energy is flat around 5; projects may be too safe.",
                   "Taste extraction has no signals yet."]
        with mock.patch.object(self_mod, "_reflect_lessons", return_value=lessons):
            r = self_mod.reflect(force=True)
        self.assertFalse(r["skipped"])
        self.assertEqual(len(r["new_lessons"]), 2)
        beliefs = self_mod.get_self_beliefs()
        self.assertEqual(len(beliefs), 2)
        self.assertTrue(all(b["source"] == "reflection" for b in beliefs))
        meta = self_mod._get_meta(self_mod._load())
        self.assertEqual(meta["rated_count"], 3)
        # re-run with the same lessons -> idempotent, no duplicates
        with mock.patch.object(self_mod, "_reflect_lessons", return_value=lessons):
            self_mod.reflect(force=True)
        self.assertEqual(len(self_mod.get_self_beliefs()), 2)


class SchedulerReflectDispatchTest(unittest.TestCase):
    def test_reflect_command_dispatches_to_reflect_cli(self):
        import argo_scheduled as sched
        self.assertEqual(sched.COMMANDS.get("reflect"), ("argo_self", "reflect_cli"))
        spy = mock.MagicMock()
        with mock.patch.object(self_mod, "reflect_cli", spy):
            sched.run_command("reflect")
        spy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
