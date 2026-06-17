"""argo_store atomic-write guarantees.

save_json must be crash-safe: a failure during the write (e.g. the rename step)
must leave the EXISTING file intact, never a truncated/half-written store. A bare
``path.write_text()`` truncates the target before writing, so an interrupted write
corrupts it -- and a corrupt store silently resets dedup/scheduler state on the
next load (load_json swallows JSONDecodeError -> default), which re-fires
already-sent alerts. This locks in the temp-file + os.replace pattern that makes
the "atomic JSON I/O" the docstring (and CLAUDE.md) promise actually true.

Pure + hermetic: tmp dir only, no network, no real data/*.json.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

import argo_store


class AtomicSaveTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = pathlib.Path(td.name)
        self.path = self.dir / "store.json"

    def test_roundtrip(self):
        argo_store.save_json(self.path, {"a": 1, "b": [1, 2]})
        self.assertEqual(argo_store.load_json(self.path, None), {"a": 1, "b": [1, 2]})

    def test_format_stays_byte_identical(self):
        # indent=2, trailing newline, no sort_keys -- seen-store/scheduler round-trip
        # files on disk and depend on this exact format.
        argo_store.save_json(self.path, {"b": 1, "a": 2})
        self.assertEqual(self.path.read_text(),
                         json.dumps({"b": 1, "a": 2}, indent=2) + "\n")

    def test_existing_file_survives_write_failure(self):
        # Negative control: if the atomic rename fails mid-write, the prior contents
        # must remain -- not a truncated/corrupt file, and no orphaned temp file.
        argo_store.save_json(self.path, {"v": 1})
        self.assertEqual(argo_store.load_json(self.path, None), {"v": 1})
        with mock.patch.object(argo_store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                argo_store.save_json(self.path, {"v": 2})
        self.assertEqual(argo_store.load_json(self.path, None), {"v": 1})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["store.json"])


if __name__ == "__main__":
    unittest.main()
