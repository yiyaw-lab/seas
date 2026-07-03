"""Seen-store regression tests — the duplicate-items area.

Locks the dedup identity, the legacy-list migration (the live data/argo_seen.json
is still in that old format), and the SEEN_CAP bound in argo_watch.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import asyncio
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

    def test_identity_query_kept_so_shared_path_items_dont_collapse(self):
        # The bug: dropping the WHOLE query collapsed every HN item (/item?id=N)
        # and YouTube video (/watch?v=N) to one id, so the feed went silent after
        # its first item. Identity params must be kept. (FAILS before the fix:
        # both ids equal "news.ycombinator.com/item".)
        self.assertNotEqual(
            watch._item_id({"link": "https://news.ycombinator.com/item?id=111"}),
            watch._item_id({"link": "https://news.ycombinator.com/item?id=222"}))
        self.assertNotEqual(
            watch._item_id({"link": "https://www.youtube.com/watch?v=AAA"}),
            watch._item_id({"link": "https://www.youtube.com/watch?v=BBB"}))
        # identity kept, tracking still dropped: the same item via a tracking URL
        # still dedups, and param order doesn't fork the id.
        self.assertEqual(
            watch._item_id({"link": "https://news.ycombinator.com/item?id=111&utm_source=x"}),
            watch._item_id({"link": "https://news.ycombinator.com/item?id=111"}))

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


class WatchPlacementSummaryTest(unittest.TestCase):
    """main()'s closing seen-store summary print must not crash when SEEN_PATH is
    on a persistent volume OUTSIDE the repo root -- that's the CORRECT production
    placement (SEEN_PATH=/data/... , ROOT=/app), not the bad one. Regression for a
    verified-live Railway crash: SEEN_PATH.relative_to(ROOT) raises ValueError
    whenever SEEN_PATH isn't a subpath of ROOT, which is exactly the volume case,
    so watch's summary print (and, when this runs inside the scheduler's
    try/except, the whole `watch` schedule entry) blew up on every run.

    Everything except the seen-store save/print tail is mocked so this stays a
    pure unit test: no network, no LLM, no real data/*.json. new_items is forced
    empty so collect_new/judge short-circuit and main() falls straight through to
    the seen-store update + summary print -- the crash site -- without touching
    a feed or the judge model.
    """

    def setUp(self):
        # A tmp dir is never under the repo ROOT, so this reproduces the volume
        # placement (SEEN_PATH outside ROOT) that crashed in production.
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "argo_seen.json"
        self.enterContext(mock.patch.object(watch, "SEEN_PATH", self.path))
        self.assertFalse(
            self.path.is_relative_to(watch.ROOT),
            "test setup bug: tmp path must be outside ROOT to reproduce the crash")

    def test_summary_print_does_not_raise_when_seen_path_outside_root(self):
        with mock.patch.object(watch, "load_seen", return_value={}), \
             mock.patch.object(watch, "collect_new", return_value=[]), \
             mock.patch.object(watch, "collect_grok", return_value=[]), \
             mock.patch.object(watch, "judge", return_value=[]), \
             mock.patch.object(watch.sys, "argv", ["argo_watch.py"]):
            watch.main()  # must not raise ValueError

        # The real side effect: the seen-store save must actually have happened
        # (this exercises the genuine save_seen call, not a mock of it).
        self.assertEqual(watch.load_seen(), {})
        self.assertTrue(self.path.exists())


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


class LruTouchTest(unittest.TestCase):
    """The news-repeats-across-weeks bug: save_seen evicts oldest-by-insertion,
    and nothing refreshed an entry's slot -- so a settled item STILL circulating
    in a feed (GitHub Trending resurfaces repos for weeks) fell off the SEEN_CAP
    edge while live and re-alerted. collect_new now LRU-touches every fetched id
    it has seen."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(watch, "SEEN_PATH",
                                            self.tmp / "argo_seen.json"))
        items = [{"link": "http://hot", "title": "hot"}]
        self.enterContext(mock.patch.object(watch.fetch_signals, "FEEDS",
                                            [("L", "http://feed")]))
        self.enterContext(mock.patch.object(watch.fetch_signals, "fetch_feed",
                                            lambda label, url: items))

    def test_settled_item_still_in_feed_survives_cap_eviction(self):
        # 'hot' was alerted long ago (settled, OLDEST slot) but is still in the
        # feed today; 'mid1'/'mid2' were seen after it and have since left the
        # feeds. With the cap at 3 the touch must keep 'hot' and evict the
        # genuinely stale 'mid1' -- before the fix 'hot' was evicted and would
        # re-alert as brand new next run.
        seen = {"hot": watch.MAX_ATTEMPTS, "mid1": 1, "mid2": 1}
        eligible = watch.collect_new(seen)
        self.assertEqual(eligible, [])              # settled: not re-judged
        seen["new1"] = 1                            # this run's fresh item
        with mock.patch.object(watch, "SEEN_CAP", 3):
            watch.save_seen(seen)
        kept = watch.load_seen()
        self.assertIn("hot", kept)
        self.assertNotIn("mid1", kept)


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

    def _status(self):
        # get_tripwire_status is an async MCP tool (with_deadline offloads it off the
        # event loop); drive it to completion for the assertion.
        return asyncio.run(self.srv.get_tripwire_status())

    def test_absent_store_says_persistent_by_design(self):
        out = self._status()
        self.assertIn("persist", out.lower())
        self.assertIn("nothing to build", out.lower())

    def test_counts_tracked_items_dict_format(self):
        self.path.write_text(json.dumps({"a": 3, "b": 1, "c": 3}))
        out = self._status()
        self.assertIn("3 news items", out)
        self.assertIn("persist", out.lower())

    def test_counts_legacy_list_format(self):
        self.path.write_text(json.dumps(["a", "b"]))
        out = self._status()
        self.assertIn("2 news items", out)


if __name__ == "__main__":
    unittest.main()
