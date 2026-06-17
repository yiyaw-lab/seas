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
    def test_prefers_link_canonicalized(self):
        # Canonical id is host+path with the scheme dropped, so http/https reprints
        # of the same URL collapse. (Was "http://x" before the syndication fix.)
        self.assertEqual(watch._item_id({"link": "  HTTP://X "}), "x")

    def test_falls_back_to_title(self):
        self.assertEqual(watch._item_id({"title": "Big Launch"}), "big launch")

    def test_empty_when_neither(self):
        self.assertEqual(watch._item_id({}), "")

    def test_url_scheme_and_trailing_slash_equivalent(self):
        # Same story via two feeds: https + trailing slash vs http, no slash.
        self.assertEqual(
            watch._item_id({"link": "https://Example.com/post/"}),
            watch._item_id({"link": "http://example.com/post"}))

    def test_tracking_query_stripped(self):
        self.assertEqual(
            watch._item_id({"link": "https://x.com/a?utm_source=feed&ref=hn"}),
            watch._item_id({"link": "https://x.com/a"}))

    def test_normalized_title_collapses_tag_and_whitespace(self):
        # No-link fallback: a [tag]-prefixed, oddly-spaced, punctuated reprint
        # dedups against the clean title.
        self.assertEqual(
            watch._item_id({"title": "[NEWS]  Big   Launch!"}),
            watch._item_id({"title": "Big Launch"}))


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
        # ids are canonicalized (host+path, scheme and '//' dropped), so the
        # seen-store keys are what _item_id derives from the feed links.
        seen = {"seen-once": 1, "settled": watch.MAX_ATTEMPTS}
        eligible = {watch._item_id(it) for it in watch.collect_new(seen)}
        self.assertIn("new", eligible)
        self.assertIn("seen-once", eligible)        # 1 < MAX_ATTEMPTS
        self.assertNotIn("settled", eligible)       # == MAX_ATTEMPTS


class TripwireStatusTest(unittest.TestCase):
    """get_tripwire_status (MCP tool) reports the REAL dedup state, so Argo stops
    guessing 'no dedup memory' and offering to build a log that already exists."""

    def setUp(self):
        import argo_mcp_server as srv
        import argo_paths
        self.srv = srv
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "argo_seen.json"
        # The tool reads argo_paths.SEEN_PATH at call time, so patch it there.
        self.enterContext(mock.patch.object(argo_paths, "SEEN_PATH", self.path))

    def test_absent_store_says_persistent_by_design(self):
        out = self.srv.get_tripwire_status()
        self.assertIn("persist", out.lower())
        self.assertIn("nothing to build", out.lower())

    def test_counts_tracked_items_dict_format(self):
        self.path.write_text(json.dumps({"a": 3, "b": 1, "c": 3}))
        out = self.srv.get_tripwire_status()
        self.assertIn("3 news items", out)
        self.assertIn("persist", out.lower())

    def test_counts_legacy_list_format(self):
        self.path.write_text(json.dumps(["a", "b"]))
        out = self.srv.get_tripwire_status()
        self.assertIn("2 news items", out)


if __name__ == "__main__":
    unittest.main()
