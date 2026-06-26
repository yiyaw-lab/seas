"""Negative-control test for argo_rate's Telegram offset store.

`save_offset` wrote `telegram_offset.json` non-atomically (plain `write_text`), so a
process killed mid-write could truncate it. Pre-fix, `load_offset` read it with a
bare `json.loads(OFFSET_PATH.read_text())`, so a truncated file raised
`json.JSONDecodeError` and crashed the next getUpdates run (a hard stop on the rate
reader). The fix routes both through `argo_store` -- a guarded read that degrades a
corrupt file to "no offset", and an atomic (temp + os.replace) write. This pins
that a corrupt offset file is tolerated and that the round-trip still works.
"""

import json
import tempfile
import unittest
from pathlib import Path

import argo_rate


class OffsetStoreRobustness(unittest.TestCase):
    def setUp(self):
        self._orig = argo_rate.OFFSET_PATH
        self.tmp = tempfile.mkdtemp()
        argo_rate.OFFSET_PATH = Path(self.tmp) / "argo" / "telegram_offset.json"

    def tearDown(self):
        argo_rate.OFFSET_PATH = self._orig

    def test_corrupt_file_returns_none_not_raises(self):
        argo_rate.OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        argo_rate.OFFSET_PATH.write_text('{"offset": 7')  # truncated mid-write
        self.assertIsNone(argo_rate.load_offset())  # must not raise

    def test_missing_file_returns_none(self):
        self.assertIsNone(argo_rate.load_offset())

    def test_round_trip(self):
        argo_rate.save_offset(4242)
        self.assertEqual(argo_rate.load_offset(), 4242)
        # still valid, hand-recoverable JSON on disk
        self.assertEqual(json.loads(argo_rate.OFFSET_PATH.read_text())["offset"], 4242)


if __name__ == "__main__":
    unittest.main()
