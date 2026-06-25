"""Forced-stop (TIER 2) tests: the decision ledger + the assert-no-sentinel gate.

PURE per CLAUDE.md: no network/LLM/real data. Orders are built inline; for the gate
itself, a generated bundle is EXTRACTED into a tmp dir and the emitted script is run as a
subprocess -- a negative control proving the gate FAILS while a sentinel survives and
PASSES once it is resolved.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import seasar_compile as sc

# Built by concatenation so this test file does NOT itself contain a matchable sentinel.
TOKEN = "SEASAR_DECIDE_" + "D1"


def order_with_decision():
    return {
        "title": "Demo",
        "tasks": [{"id": "T7", "wave": 1, "files": ["src/export/policy.ts"],
                   "depends_on": [], "acceptance": "policy enforced"}],
        "work_orders": [{"agent": "A", "task_ids": ["T7"]}],
        "decisions": [{
            "id": "D1",
            "question": "may a confidential record be exported?",
            "anchor_task": "T7",
            "anchor_file": "src/export/policy.ts",
            "options": ["allow", "deny"],
            "recommended": "deny",
            "rationale": "privacy invariant",
        }],
    }


class DecisionLedgerTest(unittest.TestCase):
    def test_normalize_assigns_ids_and_coerces(self):
        order = {"decisions": [{"question": "q1"}, {"question": "q2", "options": "allow"}]}
        sc._normalize_order(order)
        self.assertEqual([d["id"] for d in order["decisions"]], ["D1", "D2"])  # synthesized
        self.assertEqual(order["decisions"][1]["options"], ["allow"])          # scalar -> list

    def test_decisions_md_seeds_sentinel(self):
        md = sc._md_decisions(order_with_decision())
        self.assertIn(TOKEN, md)
        self.assertIn("may a confidential record be exported?", md)
        self.assertIn("deny", md)

    def test_decisions_md_empty_is_clean(self):
        import re
        md = sc._md_decisions({"decisions": []})
        # The prose documents the SEASAR_DECIDE_<id> placeholder, but NO matchable
        # sentinel (the gate's regex needs an alphanumeric id suffix) is seeded.
        self.assertIsNone(re.search(r"SEASAR_DECIDE_[A-Za-z0-9]+", md))
        self.assertIn("No open decisions", md)

    def test_work_order_references_decision_by_id_not_token(self):
        order = order_with_decision()
        sc._normalize_order(order)
        packet = sc._md_work_order(order["work_orders"][0], order)
        self.assertIn("D1", packet)
        self.assertIn("decision D1", packet)
        # the packet must NOT carry the literal sentinel (it would pin the gate red).
        self.assertNotIn(TOKEN, packet)


class AssertNoSentinelGateTest(unittest.TestCase):
    """Negative control on the emitted gate: it FAILS on a surviving sentinel, PASSES once
    resolved -- so the gate cannot silently no-op."""

    def _extract(self, order, dest):
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
            z.extractall(dest)
        return os.path.join(dest, os.listdir(dest)[0])

    def _run_gate(self, root):
        script = os.path.join(root, "scripts", "assert-no-sentinel.py")
        return subprocess.run([sys.executable, script, root],
                              capture_output=True, text=True)

    def test_gate_fails_then_passes(self):
        order = order_with_decision()
        sc._normalize_order(order)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._extract(order, tmp)
            self.assertTrue(os.path.exists(os.path.join(root, "scripts", "assert-no-sentinel.py")))
            self.assertTrue(os.path.exists(os.path.join(root, "DECISIONS.md")))
            # FORCED STOP: the unresolved sentinel still sits in DECISIONS.md.
            r1 = self._run_gate(root)
            self.assertEqual(r1.returncode, 1, r1.stdout + r1.stderr)
            self.assertIn("FORCED STOP", r1.stdout)
            # resolve it: rewrite the ledger without the sentinel.
            with open(os.path.join(root, "DECISIONS.md"), "w", encoding="utf-8") as fh:
                fh.write("# DECISIONS\n\nD1 resolved: deny, enforced at "
                         "src/export/policy.ts:10\n")
            r2 = self._run_gate(root)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("OK", r2.stdout)

    def test_gate_clean_when_no_decisions(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = self._extract(order, tmp)
            r = self._run_gate(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
