"""Draft-PR plumbing (for a follow-up feature): _open_pr's create call gains a draft
kwarg threaded from _gate_and_open / _run_propose_fix's payload, adding "draft": true
to the POST body only when requested. No caller sets it yet -- this is plumbing only --
so default behavior (no "draft" key in the body at all) must be unchanged.

Pure: _gh_write is stubbed to capture the POST body; no network/GitHub.
Run:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import argo_mcp_server as srv


class OpenPrDraftTest(unittest.TestCase):
    def _fake_gh(self, calls):
        def fake(method, path, body):
            calls.append((method, path, body))
            if method == "GET" and "/git/ref/heads/" in path:
                return True, {"object": {"sha": "basesha"}}
            if method == "POST" and "/git/refs" in path:
                return True, {"ref": "refs/heads/argo/x"}
            if method == "GET" and "/contents/" in path:
                return False, "HTTP Error 404: Not Found"
            if method == "PUT" and "/contents/" in path:
                return True, {"content": {"sha": "newsha"}}
            if method == "POST" and "/pulls" in path:
                return True, {"number": 1, "html_url": "http://pr/1",
                              "head": {"sha": "headsha"}}
            raise AssertionError(f"unexpected call: {method} {path}")
        return fake

    def test_draft_true_adds_draft_key_to_pr_body(self):
        calls = []
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh(calls)):
            ok, info = srv._open_pr("t", "d", {"src/x.py": "content"}, draft=True)
        self.assertTrue(ok)
        pulls_call = next(c for c in calls if c[0] == "POST" and "/pulls" in c[1])
        self.assertEqual(pulls_call[2]["draft"], True)

    def test_draft_default_omits_draft_key(self):
        calls = []
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh(calls)):
            ok, info = srv._open_pr("t", "d", {"src/x.py": "content"})
        self.assertTrue(ok)
        pulls_call = next(c for c in calls if c[0] == "POST" and "/pulls" in c[1])
        self.assertNotIn("draft", pulls_call[2])

    def test_draft_false_explicit_omits_draft_key(self):
        calls = []
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh(calls)):
            srv._open_pr("t", "d", {"src/x.py": "content"}, draft=False)
        pulls_call = next(c for c in calls if c[0] == "POST" and "/pulls" in c[1])
        self.assertNotIn("draft", pulls_call[2])

    def test_gate_and_open_threads_draft_through(self):
        calls = []
        with mock.patch.object(srv, "_gh_write", side_effect=self._fake_gh(calls)), \
             mock.patch.object(srv, "_proposal_gate", return_value=None):
            ok, info = srv._gate_and_open("t", "d", {"src/x.py": "content"}, draft=True)
        self.assertTrue(ok)
        pulls_call = next(c for c in calls if c[0] == "POST" and "/pulls" in c[1])
        self.assertEqual(pulls_call[2]["draft"], True)

    def test_run_propose_fix_reads_draft_from_payload(self):
        calls = []
        edits = [{"path": "src/x.py", "old": "a", "new": "b"},
                 {"path": "tests/test_x_repro.py", "new": "def test_x():\n    assert True\n"}]

        def fake_gh(method, path, body):
            if method == "GET" and "/contents/src/x.py" in path:
                import base64
                return True, {"content": base64.b64encode(b"a").decode(), "sha": "s"}
            if method == "GET" and "/contents/tests/" in path:
                return False, "HTTP Error 404: Not Found"
            return self._fake_gh(calls)(method, path, body)

        with mock.patch.object(srv, "_author_fix_edits", return_value=edits), \
             mock.patch.object(srv, "_gh_write", side_effect=fake_gh), \
             mock.patch.object(srv, "_proposal_gate", return_value=None), \
             mock.patch("argo_diagnose.append_proposal", return_value=None):
            text, info = srv._run_propose_fix(
                {"title": "t", "description": "d", "draft": True}, return_info=True)

        self.assertIsNotNone(info)
        pulls_call = next(c for c in calls if c[0] == "POST" and "/pulls" in c[1])
        self.assertEqual(pulls_call[2]["draft"], True)


if __name__ == "__main__":
    unittest.main()
