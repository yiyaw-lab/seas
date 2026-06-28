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


class Pr77BugbotFixesTest(unittest.TestCase):
    """Cursor Bugbot review of PR #77 (nightshift-costs-harden)."""

    def test_int_task_ids_still_route_the_ripple(self):  # #77-1 (str/int id mismatch)
        # Model emits INTEGER task ids + int owner/consumers. Pre-fix, _normalize_order left
        # ids as ints while depends_on/consumers stringified, so _contract_consumers MISSED
        # both the declared (3) and the derived (2) consumer -> empty blast radius.
        order = {"tasks": [{"id": 1, "depends_on": []},
                           {"id": 2, "depends_on": [1]},   # derived: depends on owner
                           {"id": 3, "depends_on": []}],   # declared below
                 "work_orders": [{"agent": "a", "task_ids": [2, 3]}]}
        sc._normalize_order(order)
        c = {"owner_task": 1, "consumers": [3]}
        ids, agents = sc._contract_consumers(order, c)
        self.assertEqual(ids, ["2", "3"])     # both consumers routed, ids stringified
        self.assertEqual(agents, ["a"])

    def test_mixed_str_int_ids_do_not_crash_sorted(self):  # #77-1 (sorted() TypeError)
        # A str declared id + an int derived id landed in one set -> sorted() crashed on
        # 'int' < 'str'. Stringifying at the comparison boundary makes it total-orderable.
        # Run WITHOUT normalization to prove the function is correct standalone (the CLI
        # gate verify-build-order.py runs on raw, un-normalized JSON).
        order = {"tasks": [{"id": "T1", "depends_on": []},
                           {"id": 2, "depends_on": ["T1"]},   # int id, derived consumer
                           {"id": "T3", "depends_on": []}],
                 "work_orders": []}
        ids, _ = sc._contract_consumers(order, {"owner_task": "T1", "consumers": ["T3"]})
        self.assertEqual(ids, ["2", "T3"])    # no crash; both routed

    def test_contract_json_ir_reports_full_blast_radius(self):  # #77-2 (declared-only IR)
        # The machine IR contracts/*.contract.json must carry the SAME unioned consumer set
        # (declared UNION derived) as the human docs -- pre-fix it wrote declared-only and
        # under-reported the blast radius vs CONTRACT_CHANGES.md.
        order = {"title": "X",
                 "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.py"]},
                           {"id": "T2", "wave": 2, "depends_on": ["T1"], "files": ["src/b.py"]},
                           {"id": "T3", "wave": 2, "files": ["src/c.py"]}],
                 "contracts": [{"name": "Api", "owner_task": "T1", "source_lang": "python",
                                "source": "x = 1", "source_path": "src/api.py",
                                "consumers": ["T3"]}],   # declared T3; T2 derived from depends_on
                 "work_orders": [{"agent": "a", "task_ids": ["T1"]},
                                 {"agent": "b", "task_ids": ["T2", "T3"]}]}
        sc._normalize_order(order)
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
            name = next(n for n in z.namelist() if n.endswith(".contract.json"))
            import json
            ir = json.loads(z.read(name).decode())
        self.assertEqual(ir["consumers"], ["T2", "T3"])   # union, not declared-only ["T3"]

    def test_has_behavior_accepts_name_keyed_op(self):  # #77-3 (op vs name)
        # _normalize_interface accepts an op keyed by `op` OR `name`; _has_behavior must too,
        # or verify run BEFORE normalization fails contracts_specify_behavior on a valid order.
        self.assertTrue(sv._has_behavior({"source": "x",
                                          "interface": [{"name": "createUser", "returns": "User"}]}))
        self.assertTrue(sv._has_behavior({"source": "x",
                                         "interface": [{"op": "createUser"}]}))
        # negative control: an op with neither op nor name is not a behavioral spec.
        self.assertFalse(sv._has_behavior({"source": "x", "interface": [{"returns": "X"}]}))

    def test_costs_summary_tolerates_explicit_null_fields(self):  # #77-4 (null -> TypeError)
        # A partial/legacy ledger line can carry an EXPLICIT JSON null for the cost/token
        # fields. .get(k, 0) returns the default only when the key is ABSENT; when present
        # and null it returns None, and sum()/// then crashed --costs. Coerce None -> 0.
        orig = sc.COSTS_PATH
        tmp = tempfile.mkdtemp()
        sc.COSTS_PATH = __import__("pathlib").Path(tmp) / "costs.jsonl"
        try:
            import json
            sc.COSTS_PATH.write_text(
                json.dumps({"id": "a", "total_cost_usd": 0.5,
                            "total_input_tokens": 100, "total_output_tokens": 50}) + "\n"
                + json.dumps({"id": "b", "total_cost_usd": None,        # explicit nulls
                              "total_input_tokens": None,
                              "total_output_tokens": None}) + "\n")
            buf = io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(buf):
                sc._print_costs_summary()   # must NOT raise
            out = buf.getvalue()
            self.assertIn("2 build orders", out)
            self.assertIn("total $0.50", out)   # the null line contributes 0
        finally:
            sc.COSTS_PATH = orig


if __name__ == "__main__":
    unittest.main()
