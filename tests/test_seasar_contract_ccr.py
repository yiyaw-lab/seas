"""Item 3b tests: the contract-change-request (CCR) freeze gate + the deterministic
merge-order. PURE per CLAUDE.md: orders inline; the freeze gate runs as a subprocess
against an extracted bundle (negative control).
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import seasar_compile as sc


def _bundle_root(order, dest):
    sc._normalize_order(order)
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        z.extractall(dest)
    return os.path.join(dest, os.listdir(dest)[0])


def _freeze(root, *args, stdin=None):
    s = os.path.join(root, "scripts", "check-contract-freeze.py")
    return subprocess.run([sys.executable, s, *args], input=stdin,
                          capture_output=True, text=True, cwd=root)


def _order_with_contract():
    return {"title": "X",
            "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.ts"]}],
            "contracts": [{"name": "Api", "owner_task": "T1",
                           "source": "export type A = {}", "source_path": "src/api.ts"}]}


class MergeOrderTest(unittest.TestCase):
    def test_topo_order_deps_before_dependents(self):
        order = {"tasks": [{"id": "T1", "wave": 1, "depends_on": []},
                           {"id": "T2", "wave": 2, "depends_on": ["T1"]},
                           {"id": "T3", "wave": 1, "depends_on": []}]}
        mo = sc._merge_order(order)
        self.assertEqual(set(mo), {"T1", "T2", "T3"})
        self.assertLess(mo.index("T1"), mo.index("T2"))   # dep before dependent
        self.assertEqual(mo, ["T1", "T3", "T2"])           # deterministic (wave,id) tiebreak

    def test_cycle_does_not_hang_and_is_deterministic(self):
        order = {"tasks": [{"id": "A", "wave": 1, "depends_on": ["B"]},
                           {"id": "B", "wave": 1, "depends_on": ["A"]}]}
        self.assertEqual(sc._merge_order(order), ["A", "B"])  # remnant by (wave,id); no hang/dup

    def test_three_cycle_remnant_has_no_duplicates(self):
        order = {"tasks": [{"id": "A", "wave": 1, "depends_on": ["C"]},
                           {"id": "B", "wave": 1, "depends_on": ["A"]},
                           {"id": "C", "wave": 1, "depends_on": ["B"]}]}
        mo = sc._merge_order(order)
        self.assertEqual(sorted(mo), ["A", "B", "C"])
        self.assertEqual(len(mo), len(set(mo)))   # each task exactly once

    def test_self_dep_and_missing_dep_ignored(self):
        self.assertEqual(sc._merge_order(
            {"tasks": [{"id": "A", "wave": 1, "depends_on": ["A"]},      # self-dep ignored
                       {"id": "B", "wave": 1, "depends_on": ["GHOST"]}]}), ["A", "B"])

    def test_duplicate_ids_not_emitted_twice(self):
        self.assertEqual(
            sc._merge_order({"tasks": [{"id": "A", "wave": 1}, {"id": "A", "wave": 1}]}), ["A"])

    def test_stamp_populates_merge_order(self):
        # input lists T2 before T1, so a pass-through would give ["T2","T1"]; topo must fix it.
        order = {"title": "X",
                 "tasks": [{"id": "T2", "wave": 2, "depends_on": ["T1"], "files": ["b.ts"]},
                           {"id": "T1", "wave": 1, "files": ["a.ts"]}],
                 "orchestration": {"waves": [["T1"], ["T2"]]}}
        sc.stamp(order)
        self.assertEqual(order["orchestration"]["merge_order"], ["T1", "T2"])


class ContractVersionTest(unittest.TestCase):
    def test_default_version_applied(self):
        order = {"contracts": [{"name": "C", "owner_task": "T1"}]}
        sc._normalize_order(order)
        self.assertEqual(order["contracts"][0]["version"], "1.0.0")


class FreezeGateTest(unittest.TestCase):
    def test_changed_contract_without_ccr_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)
            r = _freeze(root, "src/api.ts")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("CONTRACT FREEZE", r.stdout)

    def test_changed_contract_with_ccr_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)
            with open(os.path.join(root, "CONTRACT_CHANGES.md"), "a", encoding="utf-8") as fh:
                fh.write("\nCCR Api: T2 needs an extra field -- pagination\n")
            r = _freeze(root, "src/api.ts")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_non_contract_change_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)
            self.assertEqual(_freeze(root, "src/other.ts").returncode, 0)

    def test_prefix_named_contract_not_satisfied_by_longer_ccr(self):
        order = {"title": "X",
                 "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.ts", "src/apiv2.ts"]}],
                 "contracts": [
                     {"name": "Api", "owner_task": "T1", "source": "x", "source_path": "src/api.ts"},
                     {"name": "ApiV2", "owner_task": "T1", "source": "y", "source_path": "src/apiv2.ts"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(order, tmp)
            with open(os.path.join(root, "CONTRACT_CHANGES.md"), "a", encoding="utf-8") as fh:
                fh.write("\nCCR ApiV2: T2 needs a field -- reason\n")
            # Api changed with only ApiV2's CCR -> must still FAIL (no prefix false-match).
            r = _freeze(root, "src/api.ts")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("Api", r.stdout)

    def test_prose_mention_of_other_contract_does_not_satisfy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)   # contract "Api" / src/api.ts
            with open(os.path.join(root, "CONTRACT_CHANGES.md"), "a", encoding="utf-8") as fh:
                fh.write("\nCCR Other: T2 also touches the CCR Api flow -- see notes\n")
            r = _freeze(root, "src/api.ts")   # only a prose mention of Api, no real CCR line
            self.assertEqual(r.returncode, 1, r.stdout)

    def test_freeze_reads_stdin_like_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)
            self.assertEqual(_freeze(root, stdin="src/api.ts\n").returncode, 1)   # no CCR -> fail
            self.assertEqual(_freeze(root, stdin="").returncode, 0)               # empty diff -> ok

    def test_bundle_emits_ledger_merge_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(_order_with_contract(), tmp)
            for p in ("CONTRACT_CHANGES.md", "MERGE_ORDER.md",
                      os.path.join("scripts", "check-contract-freeze.py")):
                self.assertTrue(os.path.exists(os.path.join(root, p)), p)
            with open(os.path.join(root, ".github", "workflows", "seasar-gate.yml")) as fh:
                self.assertIn("check-contract-freeze.py", fh.read())


if __name__ == "__main__":
    unittest.main()
