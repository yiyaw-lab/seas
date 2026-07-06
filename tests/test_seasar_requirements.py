"""Requirement Ledger + Affordance Scanner v0 tests.

Pure stdlib. No model calls: the scanner is deterministic and the compiler hooks are
exercised with inline build orders.
"""

import io
import json
import unittest
import zipfile

import seasar_compile as sc
import seasar_requirements as sr
import seasar_verify as sv


SCAN_TEXT = (
    "List all issues across pages using cursor pagination. "
    "Cache user profiles with a TTL. "
    "Retry webhook sends with exponential backoff after a transient failure. "
    "Debounce search-as-you-type input."
)

NEW_SCAN_TEXT = (
    "Use idempotency keys to prevent duplicate payment charges. "
    "After writes, dashboards read from replicas with replica lag and need "
    "read-your-writes freshness. "
    "A queued background job processes event delivery from an event bus. "
    "Concurrent updates can race unless locking or transactions serialize writes."
)


def _req_order(reqs=None, threaded=True):
    reqs = reqs or sr.scan_sources({"idea": "List all records across pages."})
    gates = []
    if threaded:
        for r in reqs:
            gates.append({
                "name": r["gate_id"],
                "threshold": f"{r['requirement_id']} {r['counter_cue']}",
                "blocks_merge": True,
                "test_lang": "python",
                "test_path": f"tests/gates/{r['affordance']}.py",
                "test_source": "def test_gate():\n    assert True\n",
                "fixture_refs": [],
            })
    return {
        "title": "Requirement Demo",
        "tasks": [{"id": "T1", "wave": 1, "files": ["src/list.py"],
                   "depends_on": [], "acceptance": "list path works"}],
        "work_orders": [{"agent": "Agent A", "task_ids": ["T1"],
                         "definition_of_done": "tests and gates pass"}],
        "orchestration": {"waves": [["T1"]], "handoff_protocol": "merge after gates",
                          "contract_evolution": "owner amends shared contracts"},
        "quality_gates": gates,
        "latent_requirements": reqs,
    }


def _bundle_file(order, suffix):
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode()


class ScannerTest(unittest.TestCase):
    def test_four_affordances_are_detected_in_stable_order(self):
        first = sr.scan_sources({"extra": "unused", "idea": SCAN_TEXT})
        second = sr.scan_sources({"idea": SCAN_TEXT, "extra": "unused"})
        self.assertEqual(first, second)
        expected = ["pagination", "caching", "retry", "debounce"]
        self.assertEqual([r["affordance"] for r in first], expected)
        self.assertEqual([r["requirement_id"] for r in first], [
            "LR-PAGINATION-001",
            "LR-CACHING-001",
            "LR-RETRY-001",
            "LR-DEBOUNCE-001",
        ])
        for r in first:
            self.assertIn("must", r["counter_cue"])
            self.assertTrue(r["requirement_id"].startswith("LR-"))
            self.assertTrue(r["gate_id"].startswith("gate-"))

    def test_new_affordances_are_detected_after_existing_four(self):
        first = sr.scan_sources({"idea": NEW_SCAN_TEXT})
        second = sr.scan_sources({"extra": "ignored", "idea": NEW_SCAN_TEXT})
        self.assertEqual(first, second)
        self.assertEqual([r["affordance"] for r in first], [
            "idempotency",
            "stale_reads",
            "async_events",
            "race_conditions",
        ])
        self.assertEqual([r["requirement_id"] for r in first], [
            "LR-IDEMPOTENCY-001",
            "LR-STALE-READS-001",
            "LR-ASYNC-EVENTS-001",
            "LR-RACE-CONDITIONS-001",
        ])
        self.assertIn("idempotency key", first[0]["counter_cue"])
        self.assertIn("freshness boundary", first[1]["counter_cue"])
        self.assertIn("asynchronous event boundary", first[2]["counter_cue"])
        self.assertIn("serialization", first[3]["counter_cue"])

    def test_normalize_clamps_and_fills_counter_cue(self):
        req = sr.normalize_requirement({
            "id": "REQ 1!",
            "affordance": "pagination",
            "confidence": 9,
            "status": "maybe",
        })
        self.assertEqual(req["requirement_id"], "REQ1")
        self.assertEqual(req["confidence"], 1.0)
        self.assertEqual(req["status"], "open")
        self.assertIn("paginated collection", req["counter_cue"])

    def test_normalize_drops_non_object_items(self):
        reqs = sr.normalize_requirements([
            "junk",
            {"affordance": "retry"},
        ])
        self.assertEqual([r["affordance"] for r in reqs], ["retry"])

    def test_order_scan_ignores_generated_quality_gate_text(self):
        order = {
            "quality_gates": [{
                "name": "transient-resilience",
                "threshold": (
                    "Retries use bounded backoff. Idempotency keys, replica lag, "
                    "event bus delivery, and race conditions are gate-only text."
                ),
                "blocks_merge": True,
            }],
        }
        self.assertEqual(sr.scan_order(order), [])


class CompilerThreadingTest(unittest.TestCase):
    def test_cast_prompt_threads_counter_cues_into_gate_authoring_prompt(self):
        reqs = sr.scan_sources({"idea": SCAN_TEXT})
        brief = {"normalized_idea": "Build a synced issue browser",
                 "inferred_stack": "Python",
                 "assumptions": []}
        prompt = sc._cast_prompt("raw idea", brief, "mvp", 1,
                                 {"critic": "", "user": "", "ops": ""},
                                 reqs)
        self.assertIn("PRECOMPILED LATENT REQUIREMENTS", prompt)
        self.assertIn(reqs[0]["counter_cue"], prompt)
        self.assertIn(reqs[0]["gate_id"], prompt)
        self.assertIn('"latent_requirements"', sc._CAST_SCHEMA)
        self.assertIn("idempotency|stale_reads|async_events|race_conditions",
                      sc._CAST_SCHEMA)

    def test_requirements_survive_normalization_and_verification(self):
        reqs = sr.scan_sources({"idea": SCAN_TEXT})
        order = _req_order(reqs)
        sc._normalize_order(order)
        self.assertEqual(order["latent_requirements"], reqs)
        checks = {c["name"]: c for c in sv.verify_order(order)["checks"]}
        self.assertTrue(checks["latent_requirements_have_counter_cues"]["ok"])
        self.assertTrue(checks["latent_requirements_gate_threaded"]["ok"])

    def test_unthreaded_requirement_warns(self):
        order = _req_order(threaded=False)
        sc._normalize_order(order)
        check = next(c for c in sv.verify_order(order)["checks"]
                     if c["name"] == "latent_requirements_gate_threaded")
        self.assertFalse(check["ok"])
        self.assertEqual(check["severity"], sv.WARN)

    def test_raw_malformed_requirement_warns_but_normalized_bundle_is_empty(self):
        raw = {"tasks": [{"id": "T1", "wave": 1, "files": ["src/a.py"]}],
               "orchestration": {"waves": [["T1"]]},
               "latent_requirements": ["junk"]}
        raw_check = next(c for c in sv.verify_order(raw)["checks"]
                         if c["name"] == "latent_requirements_have_counter_cues")
        self.assertFalse(raw_check["ok"])

        sc._normalize_order(raw)
        self.assertEqual(raw["latent_requirements"], [])
        self.assertIn("No latent requirements", sc._md_requirements(raw))

    def test_bundle_emits_requirement_ledger_and_agent_packet_counter_cues(self):
        reqs = sr.scan_sources({"idea": SCAN_TEXT})
        order = _req_order(reqs)
        sc._normalize_order(order)

        requirements_md = _bundle_file(order, "REQUIREMENTS.md")
        packet = _bundle_file(order, "work-orders/agent-a.md")
        spec = _bundle_file(order, "spec.md")
        agents = _bundle_file(order, "AGENTS.md")
        orchestration = _bundle_file(order, "orchestration.md")
        raw = json.loads(_bundle_file(order, "build-order.json"))

        cue = reqs[0]["counter_cue"]
        self.assertIn(cue, requirements_md)
        self.assertIn(cue, packet)
        self.assertIn(cue, spec)
        self.assertIn(cue, agents)
        self.assertIn(reqs[0]["requirement_id"], orchestration)
        self.assertEqual(raw["latent_requirements"], reqs)


if __name__ == "__main__":
    unittest.main()
