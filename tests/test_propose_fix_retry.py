"""_run_propose_fix used to fail immediately on ANY _resolve_edits error, including an
anchor-miss (the drafted 'old' text matched 0 or >1 times) -- exactly the failure mode
anchor-drift (a truncated/stale view of the file) produces. Now it retries ONCE on an
anchor-miss specifically: re-author fresh against the current base, re-resolve, and only
fail if the second attempt also misses. Any other _resolve_edits error (shape, size,
path refusal) still fails immediately -- a retry can't fix those.

Pure: _author_fix_edits and _resolve_edits are stubbed; no network/LLM/GitHub.
Run:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import argo_mcp_server as srv


ANCHOR_MISS_NOT_FOUND = "the 'old' text was not found in 'src/x.py'; it must match ..."
ANCHOR_MISS_MULTI = "the 'old' text appears 3 times in 'src/x.py'; include more ..."
SHAPE_ERROR = "too many edits (6); max 5."


class ProposeFixRetryTest(unittest.TestCase):
    def test_anchor_miss_then_success_retries_once_and_lands_pr(self):
        edits_calls = []

        def fake_author(payload):
            edits_calls.append(1)
            return [{"path": "src/x.py", "old": "a", "new": "b"}]

        resolve_calls = []

        def fake_resolve(edits):
            resolve_calls.append(1)
            if len(resolve_calls) == 1:
                return None, ANCHOR_MISS_NOT_FOUND
            return {"src/x.py": "b"}, None

        with mock.patch.object(srv, "_author_fix_edits", side_effect=fake_author), \
             mock.patch.object(srv, "_resolve_edits", side_effect=fake_resolve), \
             mock.patch.object(srv, "_gate_and_open",
                               return_value=(True, {"pr_number": 1, "url": "http://pr/1",
                                                    "head_sha": "s"})), \
             mock.patch("argo_diagnose.append_proposal", return_value=None):
            text, info = srv._run_propose_fix({"title": "t", "description": "d"},
                                              return_info=True)

        self.assertEqual(len(edits_calls), 2)     # re-authored once
        self.assertEqual(len(resolve_calls), 2)    # re-resolved once
        self.assertIsNotNone(info)
        self.assertIn("http://pr/1", text)

    def test_anchor_miss_twice_fails_after_one_retry(self):
        resolve_calls = []

        def fake_resolve(edits):
            resolve_calls.append(1)
            return None, ANCHOR_MISS_MULTI

        with mock.patch.object(srv, "_author_fix_edits",
                               return_value=[{"path": "src/x.py", "old": "a", "new": "b"}]), \
             mock.patch.object(srv, "_resolve_edits", side_effect=fake_resolve):
            text, info = srv._run_propose_fix({"title": "t", "description": "d"},
                                              return_info=True)

        self.assertEqual(len(resolve_calls), 2)  # exactly one retry, then give up
        self.assertIsNone(info)
        self.assertIn("didn't resolve cleanly", text)
        self.assertIn(ANCHOR_MISS_MULTI, text)

    def test_non_anchor_resolve_error_fails_immediately_no_retry(self):
        resolve_calls = []

        def fake_resolve(edits):
            resolve_calls.append(1)
            return None, SHAPE_ERROR

        with mock.patch.object(srv, "_author_fix_edits",
                               return_value=[{"path": "src/x.py", "old": "a", "new": "b"}]), \
             mock.patch.object(srv, "_resolve_edits", side_effect=fake_resolve):
            text, info = srv._run_propose_fix({"title": "t", "description": "d"},
                                              return_info=True)

        self.assertEqual(len(resolve_calls), 1)  # no retry for a non-anchor error
        self.assertIsNone(info)
        self.assertIn(SHAPE_ERROR, text)

    def test_is_anchor_miss_classifier(self):
        self.assertTrue(srv._is_anchor_miss(ANCHOR_MISS_NOT_FOUND))
        self.assertTrue(srv._is_anchor_miss(ANCHOR_MISS_MULTI))
        self.assertFalse(srv._is_anchor_miss(SHAPE_ERROR))
        self.assertFalse(srv._is_anchor_miss(None))
        self.assertFalse(srv._is_anchor_miss(""))


if __name__ == "__main__":
    unittest.main()
