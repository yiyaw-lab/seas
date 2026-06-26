"""Negative-control tests for seasar_verify.

Strategy: build ONE fully-materialized GOOD order inline and assert it passes
(ok / strict_ok / executability 100). Then, for each structural ERROR check and
each executability WARN check, deep-copy the GOOD order, break EXACTLY one thing,
and assert that the corresponding named check fails with the right severity --
proving each gate actually fires and isn't a silent no-op.

PURE per the repo CLAUDE.md: no network, no LLM, no real data/*.json. Every order
dict is constructed inline below.
"""

import copy
import unittest

import seasar_verify as sv


def good_order():
    """A small but complete, fully-materialized order that should pass everything.

    2 waves, 3 tasks; no same-wave shared files; depends_on strictly backward;
    orchestration.waves partitions all task ids exactly once; contracts fully
    materialized (source + source_path + valid owner_task); a materialized fixture
    plus a task whose test mentions a fixture; a runnable scaffold (manifest +
    test/CI file + .env.example); non-empty handoff_protocol + contract_evolution;
    work_orders each with a non-empty definition_of_done.
    """
    return {
        "tasks": [
            {
                "id": "t1",
                "wave": 1,
                "files": ["src/api.ts"],
                "depends_on": [],
                "test": "unit tests for api",
            },
            {
                "id": "t2",
                "wave": 1,
                "files": ["src/db.ts"],
                "depends_on": [],
                "test": "loads the seed fixture and asserts rows",
            },
            {
                "id": "t3",
                "wave": 2,
                "files": ["src/app.ts"],
                "depends_on": ["t1", "t2"],
                "test": "integration test",
            },
        ],
        "contracts": [
            {
                "name": "ApiTypes",
                "kind": "types",
                "owner_task": "t1",
                "detail": "shared request/response types",
                "source": "export interface Req { id: string }",
                "source_path": "src/contracts/api.ts",
                "behavior": {"errors": "4xx as Problem+JSON; 404 returns null, never throws"},
            },
        ],
        "fixtures": [
            {
                "path": "fixtures/seed.json",
                "body": '{"rows": [{"id": 1}]}',
            },
        ],
        "scaffold_files": [
            {"path": "package.json", "body": '{"name": "app"}'},
            {"path": "vitest.config.ts", "body": "export default {}"},
            {"path": ".env.example", "body": "API_KEY="},
        ],
        "orchestration": {
            "topology": "wave",
            "waves": [["t1", "t2"], ["t3"]],
            "consistency_check": "verify-build-order.py",
            "handoff_protocol": "each agent opens a PR; merge in wave order",
            "contract_evolution": "propose changes to the contract owner first",
        },
        "work_orders": [
            {"agent": "agent-a", "definition_of_done": "tests pass for t1"},
            {"agent": "agent-b", "definition_of_done": "tests pass for t2 and t3"},
        ],
    }


def find_check(result, name):
    """Fetch a single check by name; fail loudly if it is absent."""
    for c in result["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(
        "check %r not present; checks=%r"
        % (name, [c["name"] for c in result["checks"]]))


class GoodOrderTest(unittest.TestCase):
    """The positive control: a complete order passes cleanly."""

    def test_good_order_passes_strict(self):
        result = sv.verify_order(good_order())
        self.assertIs(result["ok"], True)
        self.assertIs(result["strict_ok"], True)
        self.assertEqual(result["executability"], 100)
        self.assertEqual(result["summary"]["errors"], 0)
        self.assertEqual(result["summary"]["warnings"], 0)
        # every check should be ok in the strict-pass case
        for c in result["checks"]:
            self.assertTrue(c["ok"], "expected pass: %s (%s)" % (c["name"], c["detail"]))

    def test_good_executability_factors_all_100(self):
        f = sv.executability_factors(good_order())
        self.assertEqual(f["contracts_compile"], 100)
        self.assertEqual(f["fixtures_materialized"], 100)
        self.assertEqual(f["scaffold_runnable"], 100)


class StructuralErrorChecksFire(unittest.TestCase):
    """Each ERROR gate must fail (and flip ok->False) when its invariant breaks."""

    def assert_error_fires(self, order, check_name):
        result = sv.verify_order(order)
        c = find_check(result, check_name)
        self.assertFalse(c["ok"], "expected %s to FAIL" % check_name)
        self.assertEqual(c["severity"], sv.ERROR)
        self.assertIs(result["ok"], False, "an ERROR check failure must set ok=False")
        return result

    def test_tasks_present_fires(self):
        order = good_order()
        order["tasks"] = []
        # waves must also be cleared so this isolates tasks_present as an error;
        # an empty task list still makes tasks_present the structural failure.
        order["orchestration"]["waves"] = []
        self.assert_error_fires(order, "tasks_present")

    def test_wave_file_disjoint_fires(self):
        order = good_order()
        # t1 and t2 are both in wave 1; make them share a file.
        order["tasks"][1]["files"] = ["src/api.ts"]
        self.assert_error_fires(order, "wave_file_disjoint")

    def test_deps_exist_fires(self):
        order = good_order()
        order["tasks"][2]["depends_on"] = ["t1", "ghost"]
        self.assert_error_fires(order, "deps_exist")

    def test_deps_point_backward_fires(self):
        order = good_order()
        # t1 (wave 1) depends on t3 (wave 2) -> a forward/same-or-later dep.
        order["tasks"][0]["depends_on"] = ["t3"]
        self.assert_error_fires(order, "deps_point_backward")

    def test_waves_partition_fires(self):
        order = good_order()
        # drop t3 from the wave listing -> waves no longer partition the tasks.
        order["orchestration"]["waves"] = [["t1", "t2"]]
        self.assert_error_fires(order, "waves_partition")

    def test_contract_owner_exists_fires(self):
        order = good_order()
        order["contracts"][0]["owner_task"] = "nope"
        self.assert_error_fires(order, "contract_owner_exists")

    def test_tasks_have_ids_fires(self):
        order = good_order()
        order["tasks"][2].pop("id")  # an id-less task must not escape the DAG checks
        self.assert_error_fires(order, "tasks_have_ids")

    def test_task_ids_unique_fires(self):
        order = good_order()
        order["tasks"][1]["id"] = "t1"  # duplicate id -> misdiagnosis without this gate
        self.assert_error_fires(order, "task_ids_unique")


class ExecutabilityWarnChecksFire(unittest.TestCase):
    """Each WARN gate must fail (but keep ok=True) when its DNA requirement is missing,
    and the matching executability factor must drop."""

    def assert_warn_fires(self, order, check_name):
        result = sv.verify_order(order)
        c = find_check(result, check_name)
        self.assertFalse(c["ok"], "expected %s to FAIL" % check_name)
        self.assertEqual(c["severity"], sv.WARN)
        self.assertIs(result["ok"], True, "a WARN failure must NOT set ok=False")
        return result

    def test_contracts_have_source_fires(self):
        order = good_order()
        order["contracts"][0]["source"] = ""
        result = self.assert_warn_fires(order, "contracts_have_source")
        self.assertLess(result["executability"], 100)
        self.assertLess(
            sv.executability_factors(order)["contracts_compile"], 100)

    def test_contracts_have_path_fires(self):
        order = good_order()
        order["contracts"][0].pop("source_path")  # source present, no landing path
        result = self.assert_warn_fires(order, "contracts_have_path")
        self.assertLess(
            sv.executability_factors(order)["contracts_compile"], 100)

    def test_fixtures_materialized_fires_when_referenced_but_removed(self):
        order = good_order()
        # remove the fixtures while t2.test still says "fixture".
        order["fixtures"] = []
        result = sv.verify_order(order)
        # both the present-if-referenced gate and the materialized gate should flag.
        present = find_check(result, "fixtures_present_if_referenced")
        materialized = find_check(result, "fixtures_materialized")
        self.assertFalse(present["ok"])
        self.assertEqual(present["severity"], sv.WARN)
        self.assertFalse(materialized["ok"])
        self.assertEqual(materialized["severity"], sv.WARN)
        self.assertIs(result["ok"], True)
        self.assertEqual(
            sv.executability_factors(order)["fixtures_materialized"], 0)

    def test_fixtures_materialized_fires_when_body_empty(self):
        order = good_order()
        # keep the fixture entry but strip its body -> not materialized.
        order["fixtures"][0]["body"] = ""
        result = self.assert_warn_fires(order, "fixtures_materialized")
        self.assertEqual(
            sv.executability_factors(order)["fixtures_materialized"], 0)
        # silence: drop the test reference too, to make sure the factor path is
        # exercised purely off fixtures[] materialization.

    def test_scaffold_runnable_fires(self):
        order = good_order()
        order["scaffold_files"] = []
        result = self.assert_warn_fires(order, "scaffold_runnable")
        self.assertEqual(
            sv.executability_factors(order)["scaffold_runnable"], 0)

    def test_handoff_protocol_present_fires(self):
        order = good_order()
        order["orchestration"]["handoff_protocol"] = ""
        self.assert_warn_fires(order, "handoff_protocol_present")

    def test_contract_evolution_present_fires(self):
        order = good_order()
        order["orchestration"]["contract_evolution"] = "   "
        self.assert_warn_fires(order, "contract_evolution_present")

    def test_work_orders_have_dod_fires(self):
        order = good_order()
        order["work_orders"][0]["definition_of_done"] = ""
        self.assert_warn_fires(order, "work_orders_have_dod")


class LegacyAndMalformedToleranceTest(unittest.TestCase):
    """A legacy-shaped or malformed order must degrade to failed WARN checks --
    never a KeyError / crash."""

    def legacy_order(self):
        """Minimal legacy shape: tasks + contracts with only the old fields +
        orchestration without ANY of the new DNA fields."""
        return {
            "tasks": [
                {"id": "t1", "wave": 1, "files": ["a.ts"], "depends_on": []},
                {"id": "t2", "wave": 2, "files": ["b.ts"], "depends_on": ["t1"]},
            ],
            "contracts": [
                {"name": "Iface", "kind": "types", "owner_task": "t1",
                 "detail": "the shared interface"},
            ],
            "orchestration": {
                "topology": "wave",
                "waves": [["t1"], ["t2"]],
                "consistency_check": "verify-build-order.py",
            },
        }

    def test_legacy_order_does_not_raise(self):
        result = sv.verify_order(self.legacy_order())
        self.assertIsInstance(result, dict)
        # legacy DAG is sound -> structural ok ...
        self.assertIs(result["ok"], True)
        # ... but the new DNA fields are absent -> WARN checks fail, not strict_ok.
        self.assertIs(result["strict_ok"], False)
        self.assertGreater(result["summary"]["warnings"], 0)
        # the missing-DNA WARN checks specifically degrade rather than KeyError:
        self.assertFalse(find_check(result, "contracts_have_source")["ok"])
        self.assertFalse(find_check(result, "scaffold_runnable")["ok"])
        self.assertFalse(find_check(result, "handoff_protocol_present")["ok"])
        self.assertFalse(find_check(result, "contract_evolution_present")["ok"])
        self.assertFalse(find_check(result, "work_orders_have_dod")["ok"])

    def test_empty_order_does_not_raise(self):
        result = sv.verify_order({})
        self.assertIsInstance(result, dict)
        # no tasks -> structural failure, but the call itself must not crash.
        self.assertFalse(find_check(result, "tasks_present")["ok"])
        self.assertIs(result["ok"], False)

    def test_none_order_does_not_raise(self):
        result = sv.verify_order(None)
        self.assertIsInstance(result, dict)
        self.assertIs(result["ok"], False)

    def test_factors_tolerate_malformed(self):
        # executability_factors must also be defensive on junk input.
        self.assertIsInstance(sv.executability_factors(None), dict)
        self.assertIsInstance(sv.executability_factors({}), dict)
        self.assertIsInstance(sv.executability_factors({"contracts": "junk"}), dict)

    def test_scalar_files_and_deps_do_not_fabricate_failures(self):
        # A model emitting files/depends_on as a bare string must NOT char-iterate into
        # phantom same-wave collisions or dangling deps.
        order = good_order()
        order["tasks"][0]["files"] = "src/only.ts"    # scalar, not a list
        order["tasks"][1]["files"] = "src/other.ts"   # scalar, not a list
        order["tasks"][2]["depends_on"] = "t1"        # scalar, not a list
        result = sv.verify_order(order)
        self.assertTrue(find_check(result, "wave_file_disjoint")["ok"],
                        "scalar files must not fabricate a collision")
        self.assertTrue(find_check(result, "deps_exist")["ok"],
                        "scalar depends_on must not fabricate dangling deps")


class FormatReportTest(unittest.TestCase):
    """format_report is a pure string builder; just confirm it renders both states."""

    def test_renders_good_and_bad(self):
        good = sv.format_report(sv.verify_order(good_order()), name="demo")
        self.assertIn("BUILD ORDER VERIFY", good)
        self.assertIn("demo", good)
        self.assertIn("structural: OK", good)   # not bare "OK" -- it substrings "BROKEN"
        self.assertNotIn("BROKEN", good)

        broken = good_order()
        broken["tasks"][1]["files"] = ["src/api.ts"]  # collision -> structural break
        report = sv.format_report(sv.verify_order(broken))
        self.assertIn("FAIL", report)
        self.assertIn("BROKEN", report)


class WorkOrderPacketTest(unittest.TestCase):
    """seasar_compile._normalize_order must coerce a string work_orders[].task_ids to a
    list so the work-order packet's allowed/forbidden/acceptance don't char-iterate."""

    def test_string_task_ids_coerced_and_packet_correct(self):
        import seasar_compile as sc
        order = {
            "tasks": [
                {"id": "T1", "wave": 1, "files": ["src/a.ts"], "depends_on": [],
                 "acceptance": "a done"},
                {"id": "T2", "wave": 1, "files": ["src/b.ts"], "depends_on": [],
                 "acceptance": "b done"},
            ],
            "contracts": [
                {"name": "c", "owner_task": "T2", "source": "x",
                 "source_path": "src/b.ts"},
            ],
            "work_orders": [{"agent": "A", "task_ids": "T1"}],  # bare-string drift case
        }
        sc._normalize_order(order)
        self.assertEqual(order["work_orders"][0]["task_ids"], ["T1"])
        packet = sc._md_work_order(order["work_orders"][0], order)
        self.assertIn("src/a.ts", packet)          # allowed (from T1)
        self.assertNotIn("- `s`", packet)          # not char-iterated into "s","r","c"...
        self.assertIn("src/b.ts", packet)          # forbidden (contract owned by T2)
        self.assertIn("a done", packet)            # T1 acceptance present


if __name__ == "__main__":
    unittest.main()
