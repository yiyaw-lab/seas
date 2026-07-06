"""Requirement diff surface v0 tests."""

import io
import unittest
import zipfile

import seasar_compile as sc


COUNTER_CUE = (
    "When a workflow reads a paginated collection, the implementation must "
    "continue until the source explicitly signals exhaustion and must never "
    "treat the first page as complete."
)


def _req(status="satisfied"):
    return {
        "requirement_id": "LR-PAGINATION-001",
        "source_span": "idea: list all records across pages",
        "affordance": "pagination",
        "counter_cue": COUNTER_CUE,
        "confidence": 0.88,
        "evidence_type": "affordance_scan",
        "gate_id": "gate-pagination-completeness",
        "status": status,
        "waiver_reason": "",
    }


def _forge():
    return {
        "forge_id": "forge-gate-pagination-completeness",
        "gate_id": "gate-pagination-completeness",
        "requirement_id": "LR-PAGINATION-001",
        "counter_cue": COUNTER_CUE,
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


def _gate():
    return {
        "name": "gate-pagination-completeness",
        "threshold": "LR-PAGINATION-001 proves complete pagination.",
        "blocks_merge": True,
        "test_lang": "python",
        "test_path": "tests/gates/pagination.py",
        "test_source": "def test_gate():\n    assert True\n",
        "fixture_refs": [],
        "gate_forge": _forge(),
    }


def _order(requirements=None, gates=None):
    return {
        "title": "Requirement Diff Demo",
        "tasks": [{"id": "T1", "wave": 1, "files": ["src/list.py"],
                   "depends_on": [], "acceptance": "tests pass"}],
        "work_orders": [{"agent": "Agent A", "role": "Backend", "task_ids": ["T1"],
                         "worktree": "wt/agent-a",
                         "definition_of_done": "tests and gates pass"}],
        "orchestration": {"waves": [["T1"]], "handoff_protocol": "merge after gates",
                          "contract_evolution": "owner amends shared contracts",
                          "consistency_check": "verify before dispatch"},
        "quality_gates": [_gate()] if gates is None else gates,
        "fixtures": [
            {"path": "tests/fixtures/golden.json", "body": "{\"ok\": true}"},
            {"path": "tests/fixtures/broken.json", "body": "{\"ok\": false}"},
        ],
        "latent_requirements": [_req()] if requirements is None else requirements,
    }


def _bundle_file(order, suffix):
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode()


class RequirementDiffTest(unittest.TestCase):
    def test_structured_rows_include_diff_gate_and_forge_state(self):
        order = _order()
        sc._normalize_order(order)
        rows = sc.requirement_diff_rows(order)
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("LR-PAGINATION-001", row["requirement_id"])
        self.assertEqual("idea: list all records across pages", row["source_span"])
        self.assertEqual(COUNTER_CUE, row["inserted_counter_cue_sentence"])
        self.assertEqual("satisfied", row["status"])
        self.assertEqual("gate-pagination-completeness", row["gate_id"])
        self.assertEqual("blocking", row["gate_state"])
        self.assertEqual("discriminates", row["forge_state"])
        self.assertEqual(
            {"accept", "edit", "reject", "waive"},
            set(row["actions"]),
        )

    def test_bundle_emits_readable_requirement_diff_markdown(self):
        order = _order()
        sc._normalize_order(order)
        diff = _bundle_file(order, "REQUIREMENT_DIFF.md")
        self.assertIn("# Requirement Diff", diff)
        self.assertIn("- requirement ID: `LR-PAGINATION-001`", diff)
        self.assertIn("- original source span: idea: list all records across pages", diff)
        self.assertIn("- inserted counter-cue sentence: " + COUNTER_CUE, diff)
        self.assertIn("- status: satisfied", diff)
        self.assertIn("- gate state: `gate-pagination-completeness` (blocking)", diff)
        self.assertIn("- forge state: discriminates", diff)
        self.assertIn("- accept: Accept this counter-cue", diff)
        self.assertIn("- edit: Edit the counter-cue", diff)
        self.assertIn("- reject: Reject this scanner finding", diff)
        self.assertIn("- waive: Waive this requirement", diff)

    def test_empty_bundle_has_explicit_empty_state(self):
        order = _order(requirements=[], gates=[])
        sc._normalize_order(order)
        self.assertEqual([], sc.requirement_diff_rows(order))
        diff = _bundle_file(order, "REQUIREMENT_DIFF.md")
        self.assertEqual(
            "# Requirement Diff\n\n"
            "No latent requirements detected by the v0 scanner.\n",
            diff,
        )


if __name__ == "__main__":
    unittest.main()
