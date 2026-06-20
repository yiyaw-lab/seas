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

    def test_refuses_protected_safety_paths(self):
        # The self-modification loops must never touch their own rails: CI under
        # .github/ and the budget/breaker guards. Refused before any GitHub write.
        for path in (".github/workflows/tests.yml", "src/argo_guard.py"):
            with mock.patch.object(srv, "_gh_write",
                                   side_effect=AssertionError("must not reach GitHub")):
                text, info = self._impl({
                    path: "x: 1\n",
                    "tests/test_rails.py": "def test_x():\n    assert True\n"})
            self.assertIsNone(info)
            self.assertIn("protected safety path", text)


class ProposeEditTest(unittest.TestCase):
    """propose_edit resolves {path, old, new} edits against current files server-side,
    then reuses the SAME validate -> gate -> open path as propose_change."""

    def _fake_get(self, current_by_path):
        import base64

        def gh(method, path, body):
            if method == "GET" and "/contents/" in path:
                # path looks like /repos/o/r/contents/src/x.py?ref=main
                p = path.split("/contents/", 1)[1].split("?", 1)[0]
                if p in current_by_path:
                    enc = base64.b64encode(current_by_path[p].encode()).decode()
                    return True, {"content": enc, "sha": "s"}
                return False, "HTTPError: HTTP Error 404: Not Found"
            raise AssertionError("must not reach a GitHub write")
        return gh

    def test_resolve_applies_unique_replacement(self):
        cur = {"src/x.py": "def main():\n    return 1\n"}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            files, err = srv._resolve_edits(
                [{"path": "src/x.py", "old": "return 1", "new": "return 2"}])
        self.assertIsNone(err)
        self.assertEqual(files["src/x.py"], "def main():\n    return 2\n")

    def test_resolve_rejects_missing_old_text(self):
        cur = {"src/x.py": "a = 1\n"}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            files, err = srv._resolve_edits(
                [{"path": "src/x.py", "old": "a = 9", "new": "a = 2"}])
        self.assertIsNone(files)
        self.assertIn("not found", err.lower())

    def test_resolve_rejects_ambiguous_old_text(self):
        cur = {"src/x.py": "x\nx\n"}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            files, err = srv._resolve_edits(
                [{"path": "src/x.py", "old": "x", "new": "y"}])
        self.assertIsNone(files)
        self.assertIn("appears 2 times", err.lower())

    def test_resolve_write_without_old_creates_absent_file(self):
        # No 'old' creates a NEW file: the existence GET 404s -> allowed.
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get({})):
            files, err = srv._resolve_edits(
                [{"path": "tests/test_new.py", "new": "def test_x():\n    assert True\n"}])
        self.assertIsNone(err)
        self.assertIn("tests/test_new.py", files)

    def test_resolve_refuses_full_overwrite_of_existing_file(self):
        # No 'old' against an EXISTING file is refused -- a full write must not clobber
        # a module Argo didn't read; changing it requires old/new.
        cur = {"src/argo_paths.py": "X = 1\n"}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            files, err = srv._resolve_edits(
                [{"path": "src/argo_paths.py", "new": "X = 99\n"}])
        self.assertIsNone(files)
        self.assertIn("already exists", err.lower())

    def test_propose_repo_ref_is_case_insensitive(self):
        # GitHub repo names are case-insensitive; a differently-cased repo arg must still
        # pin PROPOSE_BASE so reads match what propose_edit edits against.
        with mock.patch.object(srv, "PROPOSE_REPO", "yiyaw-lab/seas"), \
             mock.patch.object(srv, "PROPOSE_BASE", "main"):
            self.assertEqual(srv._propose_repo_ref("YiyaW-Lab/Seas"), "main")
            self.assertEqual(srv._propose_repo_ref("yiyaw-lab/seas"), "main")
            self.assertIsNone(srv._propose_repo_ref("other/repo"))

    def test_resolve_allows_small_edit_to_large_file(self):
        # The whole point: a small surgical edit lands in a module bigger than
        # MAX_PROPOSE_BYTES. The resolved full file may exceed the cap; the edit doesn't.
        big = "needle\n" + "x = 0\n" * 9000  # > 40KB
        cur = {"src/big.py": big}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            files, err = srv._resolve_edits(
                [{"path": "src/big.py", "old": "needle", "new": "NEEDLE"}])
        self.assertIsNone(err)
        self.assertGreater(len(files["src/big.py"].encode()), srv.MAX_PROPOSE_BYTES)

    def test_resolve_refuses_oversized_edit_payload(self):
        huge = "y" * (srv.MAX_PROPOSE_BYTES + 1)
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("no read for an oversized edit")):
            files, err = srv._resolve_edits(
                [{"path": "src/x.py", "old": "needle", "new": huge}])
        self.assertIsNone(files)
        self.assertIn("too large", err.lower())

    def test_resolve_create_not_fooled_by_404_substring(self):
        # An error merely CONTAINING '4042' is not a confirmed 404 -> fail safe (refuse).
        with mock.patch.object(srv, "_gh_write",
                               return_value=(False, "ValueError: weird code 4042")):
            files, err = srv._resolve_edits(
                [{"path": "tests/test_n.py", "new": "def test_x():\n    assert True\n"}])
        self.assertIsNone(files)
        self.assertIn("couldn't verify", err.lower())

    def test_resolve_create_fails_safe_when_existence_check_unreadable(self):
        # An inconclusive existence GET (timeout/5xx, not a 404) must NOT be treated as
        # "absent" -- refuse rather than risk clobbering an existing file.
        with mock.patch.object(srv, "_gh_write",
                               return_value=(False, "TimeoutError: timed out")):
            files, err = srv._resolve_edits(
                [{"path": "src/argo_paths.py", "new": "X = 9\n"}])
        self.assertIsNone(files)
        self.assertIn("couldn't verify", err.lower())

    def test_resolve_refuses_create_over_existing_large_file(self):
        # A >1MB existing file: the contents API returns 2xx but omits inline content.
        # That is still "exists" -- a create must be refused, not allowed to overwrite.
        with mock.patch.object(srv, "_gh_write",
                               return_value=(True, {"name": "big.py", "size": 2_000_000})):
            files, err = srv._resolve_edits(
                [{"path": "src/big.py", "new": "X = 9\n"}])
        self.assertIsNone(files)
        self.assertIn("already exists", err.lower())

    def test_resolve_refuses_protected_path_before_any_read(self):
        # A protected path is refused BEFORE any GitHub read (so _gh_write never runs).
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("must not read before validating")):
            files, err = srv._resolve_edits(
                [{"path": "src/argo_guard.py", "old": "a", "new": "b"}])
        self.assertIsNone(files)
        self.assertIn("protected safety path", err)

    def test_resolve_refuses_noop_edit(self):
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("no read for a no-op")):
            files, err = srv._resolve_edits(
                [{"path": "src/x.py", "old": "a = 1", "new": "a = 1"}])
        self.assertIsNone(files)
        self.assertIn("no change", err.lower())

    def test_resolve_refuses_too_many_edits(self):
        edits = [{"path": f"src/f{i}.py", "old": "a", "new": "b"} for i in range(6)]
        with mock.patch.object(srv, "_gh_write",
                               side_effect=AssertionError("no read past the count cap")):
            files, err = srv._resolve_edits(edits)
        self.assertIsNone(files)
        self.assertIn("too many edits", err.lower())

    def test_propose_edit_still_enforces_repro_test_gate(self):
        # Editing a src module with NO accompanying test must hit the same gate as
        # propose_change -- the edit path can't bypass the repro-test requirement.
        cur = {"src/argo_paths.py": "X = 1\n"}
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)):
            out = srv._propose_edit_impl("t", "d", json.dumps(
                [{"path": "src/argo_paths.py", "old": "X = 1", "new": "X = 2"}]))
        self.assertIn("reproduction test", out.lower())

    def test_propose_edit_opens_pr_with_resolved_files(self):
        cur = {"src/argo_paths.py": "X = 1\n"}
        repro = ("import argo_paths\n"
                 "def test_x():\n    assert argo_paths.X == 2\n")
        opened = {}

        def fake_open(title, description, files):
            opened["files"] = files
            return True, {"pr_number": 9, "url": "http://pr/9",
                          "head_sha": "abc", "branch": "argo/y"}

        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_get(cur)), \
             mock.patch.object(srv, "_open_pr", side_effect=fake_open):
            out = srv._propose_edit_impl("t", "d", json.dumps([
                {"path": "src/argo_paths.py", "old": "X = 1", "new": "X = 2"},
                {"path": "tests/test_paths_edit.py", "new": repro}]))
        self.assertIn("http://pr/9", out)
        self.assertEqual(opened["files"]["src/argo_paths.py"], "X = 2\n")  # edit applied
        self.assertIn("tests/test_paths_edit.py", opened["files"])         # test created


if __name__ == "__main__":
    unittest.main()
