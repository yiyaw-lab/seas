"""Profile loud-fallback regression test.

profile.load() falls back to a neutral DEFAULT identity when data/profile.json is
missing or corrupt -- that fallback must stay (identity can never take the bot
down), but it must be LOUD: a silent fallback writes "the builder" into the chat,
which reads like a bug instead of a missing config. These lock that a WARNING is
logged on BOTH the missing-file and corrupt-JSON paths while DEFAULT is still
returned.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import profile


class LoudProfileFallbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # The cache survives across calls; drop it so each test re-reads the file.
        self.addCleanup(profile.reload)
        profile.reload()

    def test_missing_file_logs_warning_and_returns_default(self):
        missing = self.tmp / "does_not_exist.json"
        self.assertFalse(missing.exists())
        with mock.patch.object(profile, "PROFILE_PATH", missing):
            with self.assertLogs(profile.log, level="WARNING") as cm:
                loaded = profile.load()
        self.assertEqual(loaded, profile.DEFAULT)
        self.assertTrue(any("WARNING" in line for line in cm.output))

    def test_corrupt_json_logs_warning_and_returns_default(self):
        corrupt = self.tmp / "profile.json"
        corrupt.write_text("{ this is not valid json ]")
        with mock.patch.object(profile, "PROFILE_PATH", corrupt):
            with self.assertLogs(profile.log, level="WARNING") as cm:
                loaded = profile.load()
        self.assertEqual(loaded, profile.DEFAULT)
        self.assertTrue(any("WARNING" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
