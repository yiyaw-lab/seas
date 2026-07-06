"""Gate Forge v0 tests.

Pure stdlib. These lock the deterministic part of the agentic gate-forging loop:
forged gates must carry golden/broken evidence and that evidence must survive
normalization, verification, and bundle emission.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import seasar_compile as sc
import seasar_gate_forge as gf
import seasar_requirements as sr
import seasar_verify as sv


def _req():
    return sr.scan_sources({"idea": "List all records across pages."})[0]


def _forge(status="discriminates", broken_exit=1):
    return {
        "status": status,
        "run_command": "PYTHONPATH=. python -m pytest tests/gates/pagination.py",
        "golden_fixture_ref": "tests/fixtures/pagination-golden.json",
        "broken_fixture_ref": "tests/fixtures/pagination-broken.json",
        "attempts": [{
            "attempt": "1",
            "run_command": "PYTHONPATH=. python -m pytest tests/gates/pagination.py",
            "test_path": "tests/gates/pagination.py",
            "golden_fixture_ref": "tests/fixtures/pagination-golden.json",
            "golden_exit_code": "0",
            "broken_fixture_ref": "tests/fixtures/pagination-broken.json",
            "broken_exit_code": str(broken_exit),
            "revision_note": "initial discriminating predicate",
        }],
    }


def _order(forge=None, include_broken=True):
    req = _req()
    fixtures = [{
        "path": "tests/fixtures/pagination-golden.json",
        "body": '{"pages": [[1], [2]], "next": null}',
    }]
    if include_broken:
        fixtures.append({
            "path": "tests/fixtures/pagination-broken.json",
            "body": '{"pages": [[1]], "next": "cursor-2"}',
        })
    gate = {
        "name": req["gate_id"],
        "threshold": req["requirement_id"] + " must scan until exhaustion",
        "blocks_merge": True,
        "test_lang": "python",
        "test_path": "tests/gates/pagination.py",
        "test_source": "def test_pagination_gate():\n    assert True\n",
        "fixture_refs": [f["path"] for f in fixtures],
    }
    if forge is not None:
        gate["gate_forge"] = forge
    return {
        "title": "Gate Forge Demo",
        "tasks": [{"id": "T1", "wave": 1, "files": ["src/list.py"],
                   "depends_on": [], "acceptance": "pagination gate passes"}],
        "work_orders": [{"agent": "Agent A", "task_ids": ["T1"],
                         "definition_of_done": "tests and forged gate pass"}],
        "orchestration": {"waves": [["T1"]], "handoff_protocol": "merge after gates",
                          "contract_evolution": "owner amends shared contracts"},
        "fixtures": fixtures,
        "quality_gates": [gate],
        "latent_requirements": [req],
    }


def _check(order, name):
    for c in sv.verify_order(order)["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError("missing check %s" % name)


def _bundle_file(order, suffix):
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode()


def _bundle_root(order, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        z.extractall(dest)
    return os.path.join(dest, os.listdir(dest)[0])


class GateForgeNormalizeTest(unittest.TestCase):
    def test_normalization_is_stable_and_clamps_unknown_status(self):
        raw = _forge(status="claimed")
        first = gf.normalize_gate_forge(raw, gate_name="Gate Pagination",
                                        test_path="tests/gates/pagination.py",
                                        requirement_id="LR-PAGINATION-001")
        second = gf.normalize_gate_forge(raw, gate_name="Gate Pagination",
                                         test_path="tests/gates/pagination.py",
                                         requirement_id="LR-PAGINATION-001")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["forge_id"], "forge-gate-pagination")
        self.assertEqual(first["attempts"][0]["golden_exit_code"], 0)
        self.assertEqual(first["attempts"][0]["broken_exit_code"], 1)

    def test_attempt_fields_are_promoted_to_top_level_summary(self):
        raw = {"status": "discriminates", "attempts": [_forge()["attempts"][0]]}
        normalized = gf.normalize_gate_forge(raw, gate_name="gate-pagination-completeness")
        self.assertEqual(normalized["run_command"],
                         "PYTHONPATH=. python -m pytest tests/gates/pagination.py")
        self.assertEqual(normalized["golden_fixture_ref"],
                         "tests/fixtures/pagination-golden.json")
        self.assertEqual(normalized["broken_fixture_ref"],
                         "tests/fixtures/pagination-broken.json")


class GateForgeVerifyTest(unittest.TestCase):
    def test_requirement_gate_with_forge_evidence_passes(self):
        order = _order(_forge())
        sc._normalize_order(order)
        self.assertTrue(_check(order, "gate_forge_discriminates")["ok"])

    def test_requirement_gate_without_forge_evidence_warns(self):
        order = _order()
        sc._normalize_order(order)
        check = _check(order, "gate_forge_discriminates")
        self.assertFalse(check["ok"])
        self.assertIn("missing gate_forge evidence", check["detail"])

    def test_broken_fixture_must_fail(self):
        order = _order(_forge(broken_exit=0))
        sc._normalize_order(order)
        check = _check(order, "gate_forge_discriminates")
        self.assertFalse(check["ok"])
        self.assertIn("broken fixture passed", check["detail"])

    def test_golden_and_broken_fixtures_must_be_materialized(self):
        order = _order(_forge(), include_broken=False)
        sc._normalize_order(order)
        check = _check(order, "gate_forge_discriminates")
        self.assertFalse(check["ok"])
        self.assertIn("fixture(s) not materialized", check["detail"])


class GateForgeBundleTest(unittest.TestCase):
    def test_forge_evidence_survives_bundle_outputs(self):
        order = _order(_forge())
        sc._normalize_order(order)

        forge_md = _bundle_file(order, "GATE_FORGE.md")
        packet = _bundle_file(order, "work-orders/agent-a.md")
        orchestration = _bundle_file(order, "orchestration.md")
        raw = json.loads(_bundle_file(order, "build-order.json"))

        self.assertIn("gate-pagination-completeness", forge_md)
        self.assertIn("status: discriminates", forge_md)
        self.assertIn("golden exit 0; broken exit 1", forge_md)
        self.assertIn("forge `discriminates`", packet)
        self.assertIn("forge `discriminates`", orchestration)
        self.assertEqual(raw["quality_gates"][0]["gate_forge"]["status"],
                         "discriminates")

    def test_bundle_emits_gate_forge_packet_and_ci_gate(self):
        order = _order(_forge())
        sc._normalize_order(order)

        packet = _bundle_file(order, "gate-forge/gate-pagination-completeness.md")
        workflow = _bundle_file(order, ".github/workflows/seasar-gate.yml")
        selftest = _bundle_file(order, "scripts/selftest-gates.py")

        self.assertIn("Gate Forge Packet", packet)
        self.assertIn("LR-PAGINATION-001", packet)
        self.assertIn("Required evidence shape", packet)
        self.assertIn("python3 scripts/check-gate-forge.py", workflow)
        self.assertIn("check-gate-forge.py", selftest)

    def test_generated_gate_forge_script_passes_and_fails(self):
        good = _order(_forge())
        sc._normalize_order(good)
        bad = _order()
        sc._normalize_order(bad)

        with tempfile.TemporaryDirectory() as tmp:
            good_root = _bundle_root(good, os.path.join(tmp, "good"))
            bad_root = _bundle_root(bad, os.path.join(tmp, "bad"))
            script = os.path.join(good_root, "scripts", "check-gate-forge.py")
            good_run = subprocess.run([sys.executable, script, good_root],
                                      capture_output=True, text=True)
            bad_run = subprocess.run([sys.executable, script, bad_root],
                                     capture_output=True, text=True)

        self.assertEqual(good_run.returncode, 0, good_run.stdout + good_run.stderr)
        self.assertNotEqual(bad_run.returncode, 0)
        self.assertIn("missing gate_forge evidence", bad_run.stdout + bad_run.stderr)


if __name__ == "__main__":
    unittest.main()
