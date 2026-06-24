"""Repro: every Firecrawl HTTP call must pass an explicit, bounded timeout to
urlopen so a hung request fails fast (<=30s) instead of stalling the MCP slot
until the 300s ceiling. On the old code path search_related relied on an
implicit default; this test pins the explicit, bounded value end to end.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import firecrawl_client


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_timeout_is_bounded_and_explicit(monkeypatch):
    os.environ["FIRECRAWL_API_KEY"] = "test-key"
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["timeout"] = timeout
        return _FakeResp(b'{"success": true, "data": {"web": []}}')

    monkeypatch.setattr(firecrawl_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(firecrawl_client, "_ctx", lambda: None)

    res = firecrawl_client.search_related("x", allowed_hosts=None)
    assert res == []
    # An explicit timeout must be passed (never None) and stay fast.
    assert captured["timeout"] is not None
    assert captured["timeout"] <= 30


def test_scrape_timeout_bounded(monkeypatch):
    os.environ["FIRECRAWL_API_KEY"] = "test-key"
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["timeout"] = timeout
        return _FakeResp(b'{"success": true, "data": {"markdown": "hi"}}')

    monkeypatch.setattr(firecrawl_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(firecrawl_client, "_ctx", lambda: None)

    out = firecrawl_client.scrape("https://example.com")
    assert out == "hi"
    assert captured["timeout"] is not None
    assert captured["timeout"] <= 30
