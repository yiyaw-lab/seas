"""Build-time enforcement tests (TIER 2 item 3): the emitted check-ownership lint + the CI
gate workflow. PURE per CLAUDE.md: orders built inline; the lint is run as a subprocess
against an extracted bundle (negative control), like the forced-stop gate.
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


def _run(root, *args, stdin=None):
    script = os.path.join(root, "scripts", "check-ownership.py")
    return subprocess.run([sys.executable, script, *args],
                          input=stdin, capture_output=True, text=True, cwd=root)


class OwnershipBundleTest(unittest.TestCase):
    def test_bundle_emits_lint_and_ci_gate(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(order, tmp)
            self.assertTrue(os.path.exists(os.path.join(root, "scripts", "check-ownership.py")))
            self.assertTrue(os.path.exists(
                os.path.join(root, ".github", "workflows", "seasar-gate.yml")))


class OwnershipAuditTest(unittest.TestCase):
    def test_single_writer_plan_passes(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]},
                                         {"id": "T2", "wave": 1, "files": ["b.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(order, tmp))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_double_writer_plan_fails(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["shared.ts"]},
                                         {"id": "T2", "wave": 2, "files": ["shared.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(order, tmp))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("OWNERSHIP VIOLATION", r.stdout)


class OwnershipLaneTest(unittest.TestCase):
    def _order(self):
        return {"title": "X",
                "tasks": [{"id": "T1", "wave": 1, "files": ["src/a.ts"]},
                          {"id": "T2", "wave": 1, "files": ["src/b.ts"]}],
                "work_orders": [{"agent": "A", "task_ids": ["T1"]},
                                {"agent": "B", "task_ids": ["T2"]}]}

    def test_in_lane_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(self._order(), tmp), "--agent", "A", "src/a.ts")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_out_of_lane_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(self._order(), tmp), "--agent", "A", "src/b.ts")  # B's file
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("OUT OF LANE", r.stdout)

    def test_changed_files_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(self._order(), tmp), "--agent", "A", stdin="src/b.ts\n")
            self.assertEqual(r.returncode, 1, r.stdout)


if __name__ == "__main__":
    unittest.main()
