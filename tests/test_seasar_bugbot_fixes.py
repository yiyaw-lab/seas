"""Regressions for the Cursor Bugbot review of PRs #70-#75 -- defects my own adversarial
reviews missed. PURE per CLAUDE.md: orders inline; emitted gates run as subprocesses.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import seasar_compile as sc
import seasar_verify as sv


def _norm(order):
    sc._normalize_order(order)
    return order


class VerifyFixesTest(unittest.TestCase):
    def test_scalar_files_same_path_collide(self):  # #70-3 (was hidden by _strs -> [])
        order = {"tasks": [{"id": "T1", "wave": 1, "files": "a.py"},
                           {"id": "T2", "wave": 1, "files": "a.py"}],
                 "orchestration": {"waves": [["T1", "T2"]]}}
        c = next(c for c in sv.verify_order(order)["checks"] if c["name"] == "wave_file_disjoint")
        self.assertFalse(c["ok"])   # two same-wave tasks writing "a.py" must collide

    def test_waves_schedule_deps_fires(self):  # #70-4 (partition-by-set ignored grouping)
        order = {"tasks": [{"id": "T1", "wave": 1, "files": ["a"]},
                           {"id": "T2", "wave": 2, "depends_on": ["T1"], "files": ["b"]}],
                 "orchestration": {"waves": [["T1", "T2"]]}}   # T2 scheduled with its dep
        result = sv.verify_order(order)
        self.assertFalse(next(c for c in result["checks"]
                              if c["name"] == "waves_schedule_deps")["ok"])
        self.assertIs(result["ok"], False)

    def test_null_wave_entry_does_not_crash(self):  # #70-1 (TypeError on null wave)
        waves = [None, ["T1"]]
        sv.verify_order({"tasks": [{"id": "T1", "wave": 1, "files": ["a"]}],
                         "orchestration": {"waves": waves}})           # must not raise
        sc.stamp(_norm({"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a"]}],
                        "orchestration": {"waves": [None, ["T1"]]}}))  # must not raise

    def test_prose_only_gate_lowers_self_check(self):  # #72-2 (gate WARN was off the score)
        order = _norm({"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["a"]}],
                       "orchestration": {"waves": [["T1"]], "handoff_protocol": "h",
                                         "contract_evolution": "c"},
                       "work_orders": [{"agent": "A", "task_ids": ["T1"],
                                        "definition_of_done": "d"}],
                       "quality_gates": [{"name": "g", "threshold": "x",
                                          "blocks_merge": True}]})  # blocking, prose-only
        # handoff/evolution/dod all pass; only gates_have_predicates fails -> folded in now.
        self.assertLess(sv.verify_order(order)["independent_executability"], 100)


class GateFixesTest(unittest.TestCase):
    def _root(self, order, dest):
        sc._normalize_order(order)
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
            z.extractall(dest)
        return os.path.join(dest, os.listdir(dest)[0])

    def test_resolved_prefix_does_not_satisfy(self):  # #72-3 (RESOLVED_D10 satisfied D1)
        order = {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["p"]}],
                 "decisions": [{"id": "D1", "question": "q", "anchor_task": "T1"},
                               {"id": "D10", "question": "q2", "anchor_task": "T1"}]}
        with tempfile.TemporaryDirectory() as t:
            root = self._root(order, t)
            with open(os.path.join(root, "DECISIONS.md"), "w", encoding="utf-8") as fh:
                fh.write("# DECISIONS\nRESOLVED_D10: deny | enforced at p:1\n")  # only D10
            r = subprocess.run([sys.executable,
                                os.path.join(root, "scripts", "assert-no-sentinel.py"), root],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout)   # D1 still lacks RESOLVED_D1
            self.assertIn("D1", r.stdout)

    def test_agent_mode_ignores_no_lane_files(self):  # #73-3 (agent rejected tasks.md etc.)
        order = {"title": "X",
                 "tasks": [{"id": "T1", "wave": 1, "files": ["src/a.ts"]},
                           {"id": "T2", "wave": 1, "files": ["src/b.ts"]}],
                 "work_orders": [{"agent": "A", "task_ids": ["T1"]},
                                 {"agent": "B", "task_ids": ["T2"]}]}
        with tempfile.TemporaryDirectory() as t:
            root = self._root(order, t)
            s = os.path.join(root, "scripts", "check-ownership.py")
            ok = subprocess.run([sys.executable, s, "--agent", "A", "src/a.ts", "tasks.md"],
                                capture_output=True, text=True, cwd=root)
            self.assertEqual(ok.returncode, 0, ok.stdout)   # own file + shared no-lane file
            bad = subprocess.run([sys.executable, s, "--agent", "A", "src/b.ts"],
                                 capture_output=True, text=True, cwd=root)
            self.assertEqual(bad.returncode, 1, bad.stdout)  # B's file -> still out of lane


if __name__ == "__main__":
    unittest.main()
