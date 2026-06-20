"""github_read_file range + cap tests (argo_github.gh_read_file).

Argo must be able to read a whole module to rewrite it (propose_change resubmits the
full file), and to read just a span of a large file cheaply. gh_read_file backs the
github_read_file MCP tool: a full read capped at max_chars, plus a 1-based line window
via offset/limit. gh_api is faked -- no network.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import argo_github


class GhReadFileTest(unittest.TestCase):
    BODY = "\n".join(f"line{i}" for i in range(1, 11))  # line1 .. line10

    def _read(self, body=None, max_chars=40_000, **kw):
        body = self.BODY if body is None else body
        with mock.patch.object(argo_github, "gh_api", return_value=(True, body)), \
             mock.patch.object(argo_github, "repo_allowed", return_value=True):
            return argo_github.gh_read_file("o/r", "f.py", max_chars, **kw)

    def test_full_read_returns_whole_file(self):
        self.assertEqual(self._read(), self.BODY)

    def test_line_window(self):
        # offset=3 (1-based), limit=2 -> line3, line4
        self.assertEqual(self._read(offset=3, limit=2), "line3\nline4")

    def test_offset_runs_to_end(self):
        self.assertEqual(self._read(offset=9), "line9\nline10")

    def test_limit_from_start(self):
        self.assertEqual(self._read(limit=2), "line1\nline2")

    def test_cap_truncates(self):
        self.assertEqual(self._read(body="x" * 100, max_chars=10), "x" * 10)

    def test_out_of_range_window_distinct_from_empty(self):
        out = self._read(offset=99, limit=5)
        self.assertIn("no lines in that range", out)
        self.assertNotEqual(out, "(empty file)")

    def test_ref_is_passed_to_api(self):
        seen = {}

        def fake_api(path, raw=False):
            seen["path"] = path
            return True, "x\n"

        with mock.patch.object(argo_github, "gh_api", side_effect=fake_api), \
             mock.patch.object(argo_github, "repo_allowed", return_value=True):
            argo_github.gh_read_file("o/r", "f.py", 40_000, ref="main")
        self.assertIn("?ref=main", seen["path"])

    def test_error_passthrough(self):
        with mock.patch.object(argo_github, "gh_api",
                               return_value=(False, "GitHub API error: boom")), \
             mock.patch.object(argo_github, "repo_allowed", return_value=True):
            self.assertEqual(argo_github.gh_read_file("o/r", "f.py", 40_000),
                             "GitHub API error: boom")


if __name__ == "__main__":
    unittest.main()
