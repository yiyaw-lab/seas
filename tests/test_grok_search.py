"""Grok live-search source tests — parsing, opt-in gating, and the additive,
deduped wiring into the tripwire (argo_watch.collect_grok).

Pure + hermetic: no network. The xAI call (grok_search.fetch / _post) is patched;
parsing is tested directly on canned model text.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import os
import unittest
from unittest import mock

import argo_watch as watch
import grok_search


class IsEnabledTest(unittest.TestCase):
    """Opt-in: needs BOTH the key and the cost switch, so a paid call never fires
    on a key-only deploy or in tests by default."""

    def test_off_without_switch(self):
        with mock.patch.dict(os.environ, {"XAI_API_KEY": "k", "ARGO_GROK_SOURCE": "0"}):
            self.assertFalse(grok_search.is_enabled())

    def test_off_without_key(self):
        env = {"ARGO_GROK_SOURCE": "1"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("XAI_API_KEY", None)
            self.assertFalse(grok_search.is_enabled())

    def test_on_with_both(self):
        with mock.patch.dict(os.environ, {"XAI_API_KEY": "k", "ARGO_GROK_SOURCE": "1"}):
            self.assertTrue(grok_search.is_enabled())


class ParseItemsTest(unittest.TestCase):
    def test_plain_json_array(self):
        text = json.dumps([
            {"title": "Big Model", "url": "https://lab.com/x", "summary": "ships"},
            {"title": "Tool", "url": "https://t.com/y", "summary": "useful"},
        ])
        items = grok_search._parse_items(text)
        self.assertEqual([i["link"] for i in items],
                         ["https://lab.com/x", "https://t.com/y"])
        self.assertEqual(items[0]["title"], "Big Model")

    def test_strips_fence_and_prose(self):
        text = ('Here you go:\n```json\n'
                '[{"title":"A","url":"https://a.com","summary":"s"}]\n```\nDone.')
        items = grok_search._parse_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://a.com")

    def test_drops_items_without_real_url(self):
        text = json.dumps([
            {"title": "No url", "summary": "x"},
            {"title": "Bad", "url": "notaurl", "summary": "x"},
            {"title": "Good", "url": "https://ok.com", "summary": "x"},
        ])
        items = grok_search._parse_items(text)
        self.assertEqual([i["link"] for i in items], ["https://ok.com"])

    def test_garbage_returns_empty(self):
        self.assertEqual(grok_search._parse_items("no json here"), [])
        self.assertEqual(grok_search._parse_items("[not json"), [])
        self.assertEqual(grok_search._parse_items(""), [])

    def test_extract_text_from_responses_shape(self):
        data = {"output": [
            {"type": "reasoning", "content": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": "[]"}]},
        ]}
        self.assertEqual(grok_search._extract_text(data), "[]")


class FetchGatingTest(unittest.TestCase):
    def test_disabled_makes_no_call(self):
        with mock.patch.object(grok_search, "is_enabled", lambda: False), \
             mock.patch.object(grok_search, "_post") as post:
            self.assertEqual(grok_search.fetch(), [])
            post.assert_not_called()

    def test_enabled_parses_post_result(self):
        canned = {"output": [{"type": "message", "content": [
            {"type": "output_text",
             "text": json.dumps([{"title": "T", "url": "https://u.com", "summary": "s"}])}]}]}
        with mock.patch.object(grok_search, "is_enabled", lambda: True), \
             mock.patch.object(grok_search, "_post", lambda body, timeout: canned):
            items = grok_search.fetch()
        self.assertEqual(items, [{"title": "T", "link": "https://u.com", "summary": "s"}])


class CollectGrokTest(unittest.TestCase):
    """collect_grok is additive and deduped against the seen-store AND the items
    already pulled this run."""

    def test_dedups_against_seen_and_already(self):
        grok_items = [
            {"link": "http://new", "title": "new"},        # fresh -> kept
            {"link": "http://dup", "title": "dup"},         # already this run -> drop
            {"link": "http://settled", "title": "settled"}, # settled in seen -> drop
        ]
        seen = {watch._item_id({"link": "http://settled"}): watch.MAX_ATTEMPTS}
        already = [{"link": "http://dup", "title": "dup"}]
        with mock.patch.object(watch, "_item_id", wraps=watch._item_id), \
             mock.patch("grok_search.is_enabled", lambda: True), \
             mock.patch("grok_search.fetch", lambda: grok_items):
            out = watch.collect_grok(seen, already)
        self.assertEqual([i["link"] for i in out], ["http://new"])

    def test_disabled_returns_empty(self):
        with mock.patch("grok_search.is_enabled", lambda: False):
            self.assertEqual(watch.collect_grok({}, []), [])


if __name__ == "__main__":
    unittest.main()
