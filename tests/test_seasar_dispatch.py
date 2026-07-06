"""Dispatch Readiness Gate v0 tests."""

import io
import json
import unittest
import zipfile

import seasar_compile as sc
import seasar_dispatch as sd
import seasar_verify as sv


def _req(status="accepted", waiver_reason=""):
    return {
        "requirement_id": "LR-PAGINATION-001",
        "source_span": "idea: list all records across pages",
        "affordance": "pagination",
        "counter_cue": (
            "When a workflow reads a paginated collection, the implementation must "
            "continue until the source explicitly signals exhaustion and must never "
            "treat the first page as complete."
        ),
        "confidence": 0.88,
        "evidence_type": "affordance_scan",
        "gate_id": "gate-pagination-completeness",
        "status": status,
        "waiver_reason": waiver_reason,
    }


def _forge():
    return {
        "forge_id": "forge-gate-pagination-completeness",
        "gate_id": "gate-pagination-completeness",
        "requirement_id": "LR-PAGINATION-001",
        "counter_cue": _req()["counter_cue"],
        "status": "discriminates",
        "run_command": "python3 tests/gates/pagination.py",
        "golden_fixture_ref": "tests/fixtures/golden.json",
        "broken_fixture_ref": "tests/fixtures/broken.json",
        "attempts": [{
            "attempt": 1,
            "run_command": "python3 tests/gates/pagination.py",
            "test_path": "tests/gates/pagination.py",
            "golden_fixture_ref": "tests/fixtures/golden.json",
            "golden_exit_code": 0,
            "broken_fixture_ref": "tests/fixtures/broken.json",
            "broken_exit_code": 1,
            "revision_note": "initial discriminating evidence",
        }],
    }


def _gate(with_forge=True):
    gate = {
        "name": "gate-pagination-completeness",
        "threshold": "LR-PAGINATION-001 proves complete pagination.",
        "blocks_merge": True,
        "test_lang": "python",
        "test_path": "tests/gates/pagination.py",
        "test_source": "def test_gate():\n    assert True\n",
        "fixture_refs": [],
    }
    if with_forge:
        gate["gate_forge"] = _forge()
    return gate


def _order(req=None, gates=None):
    return {
        "title": "Dispatch Demo",
        "tasks": [{"id": "T1", "wave": 1, "files": ["src/list.py"],
                   "depends_on": [], "acceptance": "tests pass"}],
        "work_orders": [{"agent": "Agent A", "role": "Backend", "task_ids": ["T1"],
                         "worktree": "wt/agent-a",
                         "definition_of_done": "tests and gates pass"}],
        "orchestration": {"waves": [["T1"]], "handoff_protocol": "merge after gates",
                          "contract_evolution": "owner amends shared contracts",
                          "consistency_check": "verify before dispatch"},
        "quality_gates": gates if gates is not None else [_gate()],
        "fixtures": [
            {"path": "tests/fixtures/golden.json", "body": "{\"ok\": true}"},
            {"path": "tests/fixtures/broken.json", "body": "{\"ok\": false}"},
        ],
        "latent_requirements": [req or _req()],
    }


def _dispatch_check(order):
    return next(c for c in sv.verify_order(order)["checks"]
                if c["name"] == "latent_requirements_dispatch_ready")


def _bundle_file(order, suffix):
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode()


class DispatchReadinessTest(unittest.TestCase):
    def test_open_unwaived_requirement_warns_not_ready(self):
        order = _order(_req(status="open"))
        row = sd.readiness_rows(order)[0]
        self.assertFalse(row["ready"])
        self.assertIn("status is open", row["reasons"])
        check = _dispatch_check(order)
        self.assertFalse(check["ok"])
        self.assertEqual(check["severity"], sv.WARN)

    def test_waived_with_reason_is_ready_without_gate(self):
        order = _order(_req(status="waived", waiver_reason="Out of scope for static demo"),
                       gates=[])
        row = sd.readiness_rows(order)[0]
        self.assertTrue(row["ready"])
        self.assertTrue(_dispatch_check(order)["ok"])

    def test_satisfied_requirement_with_forged_gate_is_ready(self):
        order = _order(_req(status="satisfied"))
        row = sd.readiness_rows(order)[0]
        self.assertTrue(row["ready"])
        self.assertEqual(row["forge_status"], "discriminates")
        self.assertTrue(_dispatch_check(order)["ok"])

    def test_accepted_requirement_without_forge_warns_not_ready(self):
        order = _order(_req(status="accepted"), gates=[_gate(with_forge=False)])
        row = sd.readiness_rows(order)[0]
        self.assertFalse(row["ready"])
        self.assertIn("missing gate_forge evidence", "; ".join(row["reasons"]))
        self.assertFalse(_dispatch_check(order)["ok"])

    def test_readiness_threads_into_bundle_docs_and_packets(self):
        order = _order(_req(status="satisfied"))
        sc._normalize_order(order)
        requirements = _bundle_file(order, "REQUIREMENTS.md")
        orchestration = _bundle_file(order, "orchestration.md")
        packet = _bundle_file(order, "work-orders/agent-a.md")
        raw = json.loads(_bundle_file(order, "build-order.json"))
        self.assertIn("- dispatch readiness: dispatch-ready", requirements)
        self.assertIn("## Dispatch readiness", orchestration)
        self.assertIn("Ready: 1/1 latent requirements", orchestration)
        self.assertIn("; dispatch-ready", packet)
        self.assertEqual(raw["latent_requirements"][0]["status"], "satisfied")


if __name__ == "__main__":
    unittest.main()
