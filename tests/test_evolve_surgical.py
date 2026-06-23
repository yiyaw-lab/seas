"""EVOLVE/FIX now authors SURGICAL {old->new} edits, so a change to an existing module
cannot rewrite an unrelated function as collateral -- the PR #30 / Finding_043 failure (an
unrelated main() rewritten alongside a 3-line caching change). Drives the rewired
_run_propose_fix with the model + GitHub writes faked; the surgical resolve is real, so the
structural guarantee (you can only touch the snippet you named) is what the test exercises.

Pure: no network/LLM. Run:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import base64
import unittest
from unittest import mock

import argo_mcp_server as srv


class EvolveSurgicalAuthoringTest(unittest.TestCase):
    # An existing module with the line the fix targets AND an unrelated main() that must stay
    # byte-identical. Returned as the base for the surgical edit's GitHub read.
    BASE = ("import x\n\n"
            "def target():\n    return x.call(False)\n\n"
            "def main():\n    print('important untouched logic')\n")

    def _fake_gh(self, method, path, body):
        if method == "GET" and "/contents/src/" in path:
            return True, {"content": base64.b64encode(self.BASE.encode()).decode(),
                          "sha": "s"}
        if method == "GET" and "/contents/tests/" in path:
            return False, "HTTPError: HTTP Error 404: Not Found"
        raise AssertionError(f"unexpected GitHub call: {method} {path}")

    def test_surgical_author_cannot_rewrite_unrelated_code(self):
        # The model drafts ONE surgical edit (the targeted line) + a new repro test. Even
        # though it names the module, it can only touch the snippet it named -- main() is
        # impossible to rewrite as a side effect.
        edits = [
            {"path": "src/observe_x.py", "old": "return x.call(False)",
             "new": "return x.call(True)"},
            {"path": "tests/test_observe_x_repro.py",
             "new": "import x\ndef test_x():\n    assert True\n"}]
        opened = {}

        def fake_open(title, description, files):
            opened["files"] = files
            return True, {"pr_number": 5, "url": "http://pr/5", "head_sha": "s",
                          "branch": "argo/x"}

        with mock.patch.object(srv, "_author_fix_edits", return_value=edits), \
             mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh), \
             mock.patch.object(srv, "_proposal_gate", return_value=None), \
             mock.patch.object(srv, "_open_pr", side_effect=fake_open):
            text, info = srv._run_propose_fix(
                {"title": "adopt caching", "description": "d",
                 "suspected_files": ["src/observe_x.py"]},
                return_info=True)

        self.assertIsNotNone(info)
        resolved = opened["files"]["src/observe_x.py"]
        self.assertIn("return x.call(True)", resolved)                      # change applied
        self.assertIn("def main():\n    print('important untouched logic')",
                      resolved)                                             # collateral impossible
        self.assertIn("tests/test_observe_x_repro.py", opened["files"])     # repro created

    def test_unresolvable_edits_decline_without_opening(self):
        # If the model's 'old' doesn't match the current file, the resolve fails and Argo
        # declines honestly rather than opening a shaky PR.
        edits = [{"path": "src/observe_x.py", "old": "nonexistent text", "new": "z"}]
        with mock.patch.object(srv, "_author_fix_edits", return_value=edits), \
             mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh), \
             mock.patch.object(srv, "_open_pr",
                               side_effect=AssertionError("must not open on unresolved edits")):
            text, info = srv._run_propose_fix(
                {"title": "t", "description": "d", "suspected_files": ["src/observe_x.py"]},
                return_info=True)
        self.assertIsNone(info)
        self.assertIn("didn't resolve cleanly", text.lower())


if __name__ == "__main__":
    unittest.main()
