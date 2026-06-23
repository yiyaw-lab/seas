"""Confined self-read tests (argo_github.read_local_source / code_search).

search_self and the diagnose code-context read the LOCAL checkout -- a broader surface
than the GitHub-API tools -- so the containment is the security-load-bearing piece: a
read must stay inside the repo's source tree and NEVER reach .env, data/, or outside the
root, regardless of what path the model supplies. These tests seed a tmp ROOT and prove
the allow/deny matrix and that code_search works with AND without ripgrep (the live
Railway image likely has no rg, so the stdlib fallback is the production path).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_github as g


class _TmpRootMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "data").mkdir()
        (self.root / "src" / "mod.py").write_text(
            "import os\n@with_deadline\ndef f():\n    return 1\n")
        (self.root / "src" / "two.py").write_text("@with_deadline\nx = 1\n")
        (self.root / "data" / "secret.json").write_text('{"token": "sk-XYZ"}\n')
        (self.root / ".env").write_text("ARGO_PROPOSE_TOKEN=abc\n")
        self._patch = mock.patch.object(g, "_SELFREAD_ROOT", self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class ConfinedReadTest(_TmpRootMixin, unittest.TestCase):
    def test_reads_allowed_source(self):
        self.assertIn("with_deadline", g.read_local_source("src/mod.py"))

    def test_refuses_parent_traversal(self):
        self.assertTrue(g.read_local_source("../../../etc/passwd").startswith("Refused"))

    def test_refuses_absolute_path(self):
        self.assertTrue(g.read_local_source("/etc/passwd").startswith("Refused"))

    def test_refuses_dotenv(self):
        self.assertTrue(g.read_local_source(".env").startswith("Refused"))

    def test_refuses_data_dir(self):
        self.assertTrue(g.read_local_source("data/secret.json").startswith("Refused"))

    def test_refuses_non_source_extension(self):
        (self.root / "src" / "notes.json").write_text('{"k": 1}\n')
        self.assertTrue(g.read_local_source("src/notes.json").startswith("Refused"))

    def test_window_offset_limit(self):
        self.assertEqual(g.read_local_source("src/mod.py", offset=2, limit=1).strip(),
                         "@with_deadline")


class CodeSearchTest(_TmpRootMixin, unittest.TestCase):
    def test_empty_pattern_refused(self):
        self.assertTrue(g.code_search("").startswith("Refused"))

    def test_finds_literal_across_files_with_rg_if_present(self):
        out = g.code_search("with_deadline")
        self.assertIn("src/mod.py", out)
        self.assertIn("src/two.py", out)

    def test_fallback_finds_hit_without_ripgrep(self):
        # The live (rg-absent) path: force the stdlib walk and confirm it still locates.
        with mock.patch.object(g.shutil, "which", return_value=None):
            out = g.code_search("with_deadline")
        self.assertIn("src/mod.py", out)
        self.assertIn(":", out)  # path:line:text shape

    def test_fallback_never_searches_data_or_dotenv(self):
        # Even a literal that exists ONLY in data/.env must not surface (confinement).
        with mock.patch.object(g.shutil, "which", return_value=None):
            out = g.code_search("sk-XYZ")           # lives in data/secret.json
        self.assertNotIn("secret.json", out)
        self.assertTrue(out.startswith("No matches"))

    def test_total_results_capped(self):
        big = self.root / "src" / "many.py"
        big.write_text("\n".join("needle = %d" % i for i in range(50)) + "\n")
        with mock.patch.object(g.shutil, "which", return_value=None):
            out = g.code_search("needle", max_results=5)
        self.assertLessEqual(len(out.splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
