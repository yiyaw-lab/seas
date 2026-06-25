"""Gate-predicate tests (TIER 2 item 2): the compiler authors each blocking gate's
executable test (`test_source`) and the bundle emits it at `test_path`, so the feature
agent inherits a gate it cannot tautologize. PURE per CLAUDE.md: orders built inline.
"""

import io
import os
import tempfile
import unittest
import zipfile

import seasar_compile as sc
import seasar_verify as sv


def order_with_gate(test_source="expect(drift).toBe(0)"):
    return {
        "title": "Demo",
        "tasks": [{"id": "T1", "wave": 1, "files": ["src/anchor.ts"]}],
        "orchestration": {"waves": [["T1"]]},  # structurally sound -> isolates the gate WARN
        "fixtures": [{"path": "tests/fixtures/sample.epub", "binary": True,
                      "generator": "node scripts/make-epub.js"}],
        "quality_gates": [{
            "name": "anchor-drift",
            "threshold": "0 drift across layout changes",
            "blocks_merge": True,
            "test_lang": "typescript",
            "test_path": "tests/gates/anchor-drift.test.ts",
            "test_source": test_source,
            "fixture_refs": ["tests/fixtures/sample.epub"],
        }],
    }


def gate_check(order):
    sc._normalize_order(order)
    for c in sv.verify_order(order)["checks"]:
        if c["name"] == "gates_have_predicates":
            return c
    return None


class GatePredicateVerifyTest(unittest.TestCase):
    def test_gate_with_predicate_passes(self):
        c = gate_check(order_with_gate())
        self.assertIsNotNone(c)
        self.assertTrue(c["ok"])

    def test_gate_without_predicate_warns(self):
        order = order_with_gate(test_source="")
        sc._normalize_order(order)
        result = sv.verify_order(order)
        c = next(c for c in result["checks"] if c["name"] == "gates_have_predicates")
        self.assertFalse(c["ok"])
        self.assertEqual(c["severity"], sv.WARN)
        self.assertIs(result["ok"], True)        # prose gate is a WARN, not a DAG break

    def test_no_gates_means_no_check(self):
        # an order with no blocking gates does not add the check (no false penalty).
        names = [c["name"] for c in
                 sv.verify_order({"tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]}]})["checks"]]
        self.assertNotIn("gates_have_predicates", names)

    def test_gate_with_source_but_empty_path_warns(self):
        # test_source present but no test_path -> the bundle emits nothing (evaporation).
        order = order_with_gate()
        order["quality_gates"][0]["test_path"] = ""
        c = gate_check(order)
        self.assertFalse(c["ok"])

    def test_gate_unmaterialized_fixture_warns(self):
        order = order_with_gate()
        order["fixtures"] = []  # the gate still references sample.epub, now absent
        sc._normalize_order(order)
        c = next(c for c in sv.verify_order(order)["checks"]
                 if c["name"] == "gate_fixtures_materialized")
        self.assertFalse(c["ok"])
        self.assertEqual(c["severity"], sv.WARN)

    def test_two_gates_one_path_warns(self):
        order = order_with_gate()
        clash = dict(order["quality_gates"][0])
        clash["name"] = "other-gate"  # same test_path -> one predicate dropped at bundle time
        order["quality_gates"].append(clash)
        sc._normalize_order(order)
        c = next(c for c in sv.verify_order(order)["checks"]
                 if c["name"] == "gate_predicates_distinct")
        self.assertFalse(c["ok"])


class GatePredicateBundleTest(unittest.TestCase):
    def test_bundle_emits_gate_test_at_its_path(self):
        order = order_with_gate()
        sc._normalize_order(order)
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
                z.extractall(tmp)
            root = os.path.join(tmp, os.listdir(tmp)[0])
            gate_file = os.path.join(root, "tests", "gates", "anchor-drift.test.ts")
            self.assertTrue(os.path.exists(gate_file), "gate predicate not emitted as a file")
            with open(gate_file, encoding="utf-8") as fh:
                self.assertIn("expect(drift).toBe(0)", fh.read())


if __name__ == "__main__":
    unittest.main()
