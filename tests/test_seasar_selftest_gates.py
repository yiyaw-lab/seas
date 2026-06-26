"""Task 2 (item 4 loose end): the bundle ships scripts/selftest-gates.py -- the negative
control that proves the OTHER emitted gates have teeth. PURE per CLAUDE.md: order inline,
the selftest runs as a subprocess against an extracted bundle.

Two things proven here:
  1. selftest-gates.py is emitted and PASSES when the real gates are present (each gate
     fires on its broken fixture and passes its clean fixture).
  2. The selftest itself has teeth: neuter one gate to a tautology (`exit 0`) and the
     selftest goes RED -- so a quietly-weakened gate cannot pass the build.
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


def _order():
    return {"title": "X",
            "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.ts"]}],
            "contracts": [{"name": "Api", "owner_task": "T1",
                           "source": "export type A = {}", "source_path": "src/api.ts"}]}


def _run_selftest(root):
    s = os.path.join(root, "scripts", "selftest-gates.py")
    return subprocess.run([sys.executable, s], capture_output=True, text=True, cwd=root)


class SelftestGatesTest(unittest.TestCase):
    def test_emitted_into_bundle(self):
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(_order()))) as z:
            names = z.namelist()
        self.assertTrue(any(n.endswith("scripts/selftest-gates.py") for n in names),
                        "selftest-gates.py not emitted into the bundle")

    def test_selftest_does_not_trip_the_sentinel_gate(self):
        # The selftest constructs the decision-sentinel token at runtime; the emitted file
        # must NOT contain the literal token, or assert-no-sentinel would flag it.
        self.assertNotIn("SEASAR_DECIDE_", sc._SELFTEST_GATES)

    def test_selftest_passes_with_real_gates(self):
        with tempfile.TemporaryDirectory() as d:
            root = _bundle_root(_order(), d)
            r = _run_selftest(root)
            self.assertEqual(r.returncode, 0,
                             "selftest failed with the real gates:\n" + r.stdout + r.stderr)
            self.assertIn("negative control", r.stdout)

    def test_selftest_fails_on_uncovered_gate(self):
        # A new emitted gate with no CASES entry must make the selftest red -- otherwise a
        # future gate ships with zero negative-control coverage (the teeth-prover's blind spot).
        with tempfile.TemporaryDirectory() as d:
            root = _bundle_root(_order(), d)
            with open(os.path.join(root, "scripts", "check-newgate.py"), "w") as fh:
                fh.write("import sys\nsys.exit(0)\n")
            r = _run_selftest(root)
            self.assertNotEqual(r.returncode, 0, "selftest ignored an uncovered emitted gate")
            self.assertIn("NO negative-control coverage", r.stdout + r.stderr)

    def test_selftest_has_teeth_when_a_gate_is_neutered(self):
        with tempfile.TemporaryDirectory() as d:
            root = _bundle_root(_order(), d)
            # Quietly reduce the substance prober to a tautology that always passes.
            with open(os.path.join(root, "scripts", "check-contracts-compile.py"), "w") as fh:
                fh.write("import sys\nsys.exit(0)\n")
            r = _run_selftest(root)
            self.assertNotEqual(r.returncode, 0,
                                "selftest passed even though a gate was neutered to exit 0")
            self.assertIn("NO TEETH", r.stdout)


if __name__ == "__main__":
    unittest.main()
