"""Build-time enforcement tests (TIER 2 item 3a): the emitted check-ownership lint (audit
/ lanes / agent modes) + the CI gate workflow. PURE per CLAUDE.md: orders built inline; the
lint is run as a subprocess against an extracted bundle (negative control).
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


def _two_agent_order():
    return {"title": "X",
            "tasks": [{"id": "T1", "wave": 1, "files": ["src/a.ts"]},
                      {"id": "T2", "wave": 1, "files": ["src/b.ts"]}],
            "work_orders": [{"agent": "A", "task_ids": ["T1"]},
                            {"agent": "B", "task_ids": ["T2"]}]}


class BundleEmissionTest(unittest.TestCase):
    def test_emits_lint_and_ci_referencing_all_three_gates(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(order, tmp)
            self.assertTrue(os.path.exists(os.path.join(root, "scripts", "check-ownership.py")))
            yml = os.path.join(root, ".github", "workflows", "seasar-gate.yml")
            self.assertTrue(os.path.exists(yml))
            with open(yml, encoding="utf-8") as fh:
                body = fh.read()
            for gate in ("verify-build-order.py", "assert-no-sentinel.py",
                         "check-ownership.py", "--lanes"):
                self.assertIn(gate, body, "CI yaml must wire %s" % gate)


class AuditModeTest(unittest.TestCase):
    def test_single_writer_passes(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]},
                                         {"id": "T2", "wave": 1, "files": ["b.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(_bundle_root(order, tmp)).returncode, 0)

    def test_double_writer_fails(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["shared.ts"]},
                                         {"id": "T2", "wave": 2, "files": ["shared.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(order, tmp))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("OWNERSHIP VIOLATION", r.stdout)

    def test_same_file_twice_on_one_task_is_not_a_collision(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts", "a.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(_bundle_root(order, tmp)).returncode, 0)


class AgentModeTest(unittest.TestCase):
    def test_in_lane_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _run(_bundle_root(_two_agent_order(), tmp), "--agent", "A", "src/a.ts").returncode, 0)

    def test_out_of_lane_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(_two_agent_order(), tmp), "--agent", "A", "src/b.ts")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("OUT OF LANE", r.stdout)

    def test_non_canonical_path_still_in_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _run(_bundle_root(_two_agent_order(), tmp), "--agent", "A", "./src/a.ts").returncode, 0)

    def test_changed_files_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(_two_agent_order(), tmp), "--agent", "A", stdin="src/b.ts\n")
            self.assertEqual(r.returncode, 1, r.stdout)

    def test_unknown_agent_errors_distinctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(_two_agent_order(), tmp), "--agent", "NOPE", "src/a.ts")
            self.assertEqual(r.returncode, 1)
            self.assertIn("no work order", r.stderr)
            self.assertNotIn("OUT OF LANE", r.stdout)

    def test_empty_agent_value_is_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(_bundle_root(_two_agent_order(), tmp), "--agent").returncode, 2)

    def test_empty_diff_does_not_pass_vacuously(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(_two_agent_order(), tmp), "--agent", "A", stdin="")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_duplicate_agent_work_orders_union_lanes(self):
        order = {"title": "X",
                 "tasks": [{"id": "T1", "wave": 1, "files": ["src/a.ts"]},
                           {"id": "T2", "wave": 1, "files": ["src/b.ts"]}],
                 "work_orders": [{"agent": "A", "task_ids": ["T1"]},
                                 {"agent": "A", "task_ids": ["T2"]}]}  # same agent, two WOs
        with tempfile.TemporaryDirectory() as tmp:
            # b.ts is in A's lane only via the second work order -> must be allowed.
            self.assertEqual(
                _run(_bundle_root(order, tmp), "--agent", "A", "src/b.ts").returncode, 0)


class LanesModeTest(unittest.TestCase):
    def test_single_lane_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _run(_bundle_root(_two_agent_order(), tmp), "--lanes", "src/a.ts").returncode, 0)

    def test_cross_lane_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_bundle_root(_two_agent_order(), tmp), "--lanes", "src/a.ts", "src/b.ts")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("CROSS-LANE", r.stdout)

    def test_files_in_no_lane_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _run(_bundle_root(_two_agent_order(), tmp), "--lanes", "README.md").returncode, 0)

    def test_empty_diff_does_not_pass_vacuously(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _run(_bundle_root(_two_agent_order(), tmp), "--lanes", stdin="").returncode, 1)


class LoadFailureTest(unittest.TestCase):
    def test_missing_build_order_fails_cleanly(self):
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a.ts"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = _bundle_root(order, tmp)
            os.remove(os.path.join(root, "build-order.json"))
            r = _run(root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("cannot load build-order.json", r.stderr)


if __name__ == "__main__":
    unittest.main()
