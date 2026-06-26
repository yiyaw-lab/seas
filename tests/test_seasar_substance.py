"""Item 4 tests: the substance prober -- contract source is COMPILED/parsed, not just
checked non-empty. The compile-time check (seasar_verify, python/json via compile()/
json.loads -- never executed) + the emitted build-time gate check-contracts-compile.py.
PURE per CLAUDE.md: orders inline; the gate run as a subprocess against a bundle.
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


def _verify_check(order, name):
    sc._normalize_order(order)
    for c in sv.verify_order(order)["checks"]:
        if c["name"] == name:
            return c
    return None


def _contract_order(source, lang, path="src/c"):
    return {"title": "X",
            "tasks": [{"id": "T1", "wave": 1, "files": [path]}],
            "orchestration": {"waves": [["T1"]]},
            "contracts": [{"name": "C", "owner_task": "T1", "source": source,
                           "source_lang": lang, "source_path": path}]}


class CompileTimeProbeTest(unittest.TestCase):
    def test_valid_python_passes(self):
        self.assertTrue(_verify_check(_contract_order("x = 1\n", "python"),
                                      "contracts_source_parses")["ok"])

    def test_broken_python_warns(self):
        c = _verify_check(_contract_order("def (:\n", "python"), "contracts_source_parses")
        self.assertFalse(c["ok"])
        self.assertEqual(c["severity"], sv.WARN)

    def test_bad_json_warns(self):
        c = _verify_check(_contract_order("{not json", "json"), "contracts_source_parses")
        self.assertFalse(c["ok"])

    def test_typescript_not_checked_in_process(self):
        # TS can't compile in-process -> not flagged here (the build-time gate covers it).
        self.assertTrue(_verify_check(_contract_order("export type A = }{ no", "typescript"),
                                      "contracts_source_parses")["ok"])

    def test_parse_failure_is_warn_not_structural(self):
        order = sc._normalize_order(_contract_order("def (:\n", "python"))
        self.assertIs(sv.verify_order(order)["ok"], True)   # a WARN, not a DAG break

    def test_compile_does_not_execute_source(self):
        # a contract whose source would raise/print on EXECUTION must still pass parse.
        self.assertTrue(_verify_check(
            _contract_order("import sys\nsys.exit(7)\n", "python"),
            "contracts_source_parses")["ok"])

    def test_deeply_nested_json_does_not_raise(self):
        # an adversarial source must NOT crash verify_order (the never-raises contract).
        order = sc._normalize_order(_contract_order("[" * 5000 + "]" * 5000, "json"))
        result = sv.verify_order(order)               # must not raise
        self.assertIs(result["ok"], True)             # WARN, not a crash
        c = next(c for c in result["checks"] if c["name"] == "contracts_source_parses")
        self.assertFalse(c["ok"])


class BuildTimeGateTest(unittest.TestCase):
    def _root(self, order, dest):
        sc._normalize_order(order)
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
            z.extractall(dest)
        return os.path.join(dest, os.listdir(dest)[0])

    def _run(self, root):
        s = os.path.join(root, "scripts", "check-contracts-compile.py")
        return subprocess.run([sys.executable, s], capture_output=True, text=True, cwd=root)

    def test_valid_python_compiles(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(self._root(_contract_order("x = 1\n", "python", "src/c.py"), t))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_broken_python_fails(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(self._root(_contract_order("def (:\n", "python", "src/c.py"), t))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("CONTRACT COMPILE FAILED", r.stdout)

    def test_bad_json_fails(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(self._root(_contract_order("{not json", "json", "src/c.json"), t))
            self.assertEqual(r.returncode, 1, r.stdout)

    def test_typescript_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(self._root(_contract_order("broken ts }{", "typescript", "src/c.ts"), t))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # not compiled here

    def test_bundle_emits_prober_and_ci_wires_it(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._root(_contract_order("x = 1\n", "python", "src/c.py"), t)
            self.assertTrue(os.path.exists(
                os.path.join(root, "scripts", "check-contracts-compile.py")))
            with open(os.path.join(root, ".github", "workflows", "seasar-gate.yml")) as fh:
                self.assertIn("check-contracts-compile.py", fh.read())

    def test_missing_source_file_reported(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._root(_contract_order("x = 1\n", "python", "src/c.py"), t)
            os.remove(os.path.join(root, "src", "c.py"))
            r = self._run(root)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("does not exist", r.stdout)

    def test_one_broken_among_many_fails_by_name(self):
        order = {"title": "X",
                 "tasks": [{"id": "T1", "wave": 1, "files": ["src/a.py", "src/b.py"]}],
                 "contracts": [
                     {"name": "Good", "owner_task": "T1", "source": "x = 1\n",
                      "source_lang": "python", "source_path": "src/a.py"},
                     {"name": "Bad", "owner_task": "T1", "source": "def (:\n",
                      "source_lang": "py", "source_path": "src/b.py"}]}   # py alias
        with tempfile.TemporaryDirectory() as t:
            r = self._run(self._root(order, t))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("Bad", r.stdout)
            self.assertNotIn("Good (", r.stdout)   # the valid contract isn't a failure

    def test_no_bytecode_cache_left_in_bundle(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._root(_contract_order("x = 1\n", "python", "src/c.py"), t)
            self._run(root)
            stray = []
            for _dp, dns, fns in os.walk(root):
                stray += [f for f in fns if f.endswith(".pyc")]
                stray += [d for d in dns if d == "__pycache__"]
            self.assertEqual(stray, [], "gate must write no .pyc/__pycache__: %r" % stray)


if __name__ == "__main__":
    unittest.main()
