"""Seen-store regression tests — the duplicate-items area.

Locks the dedup identity, the legacy-list migration (the live data/argo_seen.json
is still in that old format), and the SEEN_CAP bound in argo_watch.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_watch as watch


class ItemIdTest(unittest.TestCase):
    def test_prefers_link_and_normalizes(self):
        self.assertEqual(watch._item_id({"link": "  HTTP://X "}), "http://x")

    def test_falls_back_to_title(self):
        self.assertEqual(watch._item_id({"title": "Big Launch"}), "big launch")

    def test_empty_when_neither(self):
        self.assertEqual(watch._item_id({}), "")


class LoadSaveSeenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "argo_seen.json"
        self.enterContext(mock.patch.object(watch, "SEEN_PATH", self.path))

    def test_missing_file_returns_empty(self):
        self.assertEqual(watch.load_seen(), {})

    def test_legacy_list_migrates_to_settled(self):
        # Old format was a flat list; read those as already-settled so they're
        # never re-judged. This is the format live data/argo_seen.json uses today.
        self.path.write_text(json.dumps(["a", "b"]))
        self.assertEqual(watch.load_seen(),
                         {"a": watch.MAX_ATTEMPTS, "b": watch.MAX_ATTEMPTS})

    def test_corrupt_json_returns_empty(self):
        self.path.write_text("{not json")
        self.assertEqual(watch.load_seen(), {})

    def test_save_roundtrip_dict_format(self):
        watch.save_seen({"a": 1, "b": 2})
        self.assertEqual(watch.load_seen(), {"a": 1, "b": 2})

    def test_save_bounds_to_cap_keeping_newest(self):
        with mock.patch.object(watch, "SEEN_CAP", 3):
            ordered = {f"id{i}": 1 for i in range(6)}  # id0..id5, insertion order
            watch.save_seen(ordered)
            kept = watch.load_seen()
        self.assertEqual(list(kept), ["id3", "id4", "id5"])  # newest 3


class AttemptsGatingTest(unittest.TestCase):
    """collect_new's eligibility predicate: attempts < MAX_ATTEMPTS is eligible.

    Tested against a stubbed feed so it stays offline (the only seen-store test
    that touches the fetcher seam)."""

    def setUp(self):
        items = [
            {"link": "http://new", "title": "new"},
            {"link": "http://seen-once", "title": "once"},
            {"link": "http://settled", "title": "settled"},
        ]
        self.enterContext(mock.patch.object(watch.fetch_signals, "FEEDS",
                                            [("L", "http://feed")]))
        self.enterContext(mock.patch.object(watch.fetch_signals, "fetch_feed",
                                            lambda label, url: items))

    def test_only_unsettled_items_are_eligible(self):
        seen = {"http://seen-once": 1, "http://settled": watch.MAX_ATTEMPTS}
        eligible = {watch._item_id(it) for it in watch.collect_new(seen)}
        self.assertIn("http://new", eligible)
        self.assertIn("http://seen-once", eligible)        # 1 < MAX_ATTEMPTS
        self.assertNotIn("http://settled", eligible)       # == MAX_ATTEMPTS


if __name__ == "__main__":
    unittest.main()
