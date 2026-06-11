"""Propose-gate tests (argo_mcp_server): the fix-verification contract's first link.

A fix proposal can't open a PR unless it carries a reproduction test under tests/ AND
its new code is actually wired in AND the test exercises the changed code. These gates run
BEFORE any GitHub write, so a confidently-wrong or untestable fix is caught for free. This
encodes the repo's "verify fix is wired not just written" lesson as a mechanical check.

Pure + hermetic: _gh_write is patched to blow up if a refused proposal ever reaches it, and
to fake the PR open for the accept case. No network.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import unittest
from unittest import mock

import argo_mcp_server as srv


GOOD_NEW_MODULE = (
    "def helper():\n    return 1\n")
GOOD_TEST = (
    "import argo_brandnew_xyz as m\n"
    "def test_helper():\n    assert m.helper() == 1\n")


class ProposalGateTest(unittest.TestCase):
    def _impl(self, files):
        return srv._propose_change_impl("t", "d", json.dumps(files))

    def test_refuses_when_no_repro_test(self):
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("must not reach GitHub")):
            text, info = self._impl({"src/argo_brandnew_xyz.py": GOOD_NEW_MODULE})
        self.assertIsNone(info)
        self.assertIn("reproduction test", text.lower())

    def test_refuses_dangling_new_symbol(self):
        # helper() is defined in a NEW module but referenced by nothing, not even the test.
        empty_test = ("import argo_brandnew_xyz\n"
                      "def test_nothing():\n    assert True\n")
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("must not reach GitHub")):
            text, info = self._impl({
                "src/argo_brandnew_xyz.py": GOOD_NEW_MODULE,
                "tests/test_brandnew.py": empty_test})
        self.assertIsNone(info)
        self.assertIn("wired not just written", text.lower())

    def test_refuses_syntax_error(self):
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("must not reach GitHub")):
            text, info = self._impl({
                "src/argo_brandnew_xyz.py": "def broken(:\n  pass\n",
                "tests/test_brandnew.py": GOOD_TEST})
        self.assertIsNone(info)
        self.assertIn("does not parse", text.lower())

    def test_refuses_test_that_ignores_changed_module(self):
        # Use an EXISTING file so the per-symbol wire-check is skipped and the relevance
        # check is what bites: the repro test must exercise a changed module.
        unrelated_test = ("def test_unrelated():\n    assert 1 == 1\n")
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("must not reach GitHub")):
            text, info = self._impl({
                "src/argo_paths.py": "X = 1\n",
                "tests/test_unrelated.py": unrelated_test})
        self.assertIsNone(info)
        self.assertIn("does not reference", text.lower())

    def test_accepts_wired_symbol_with_relevant_repro(self):
        opened = {}

        def fake_open(title, description, files):
            opened["files"] = files
            return True, {"pr_number": 7, "url": "http://pr/7",
                          "head_sha": "abc123", "branch": "argo/x"}

        with mock.patch.object(srv, "_open_pr", side_effect=fake_open):
            text, info = self._impl({
                "src/argo_brandnew_xyz.py": GOOD_NEW_MODULE,
                "tests/test_brandnew.py": GOOD_TEST})
        self.assertIsNotNone(info)
        self.assertEqual(info["pr_number"], 7)
        self.assertIn("http://pr/7", text)
        self.assertIn("tests/test_brandnew.py", opened["files"])

    def test_gate_passes_for_modified_existing_file(self):
        # An EXISTING file gets no per-symbol wire-check (we can't diff); only the repro
        # rule + relevance apply. argo_paths.py exists in the repo.
        test_src = ("import argo_paths\n"
                    "def test_paths():\n    assert argo_paths.ROOT is not None\n")
        self.assertIsNone(srv._proposal_gate({
            "src/argo_paths.py": "X = 1\n",
            "tests/test_paths_patch.py": test_src}))


if __name__ == "__main__":
    unittest.main()
