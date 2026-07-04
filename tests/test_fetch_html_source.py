"""Tests for the HTML scrape path added to fetch_signals.py."""
import sys
from pathlib import Path
import types
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _make_html(links):
    """Build a minimal HTML page with the given (href, text) pairs."""
    anchors = "".join(f'<a href="{h}">{t}</a>' for h, t in links)
    return f"<html><body>{anchors}</body></html>".encode()


def _patch_fetch(monkeypatch, raw_bytes):
    import fetch_signals
    monkeypatch.setattr(fetch_signals, "_fetch_url", lambda url, **kw: raw_bytes)


def test_html_source_returns_items(monkeypatch):
    import fetch_signals
    html = _make_html([
        ("/articles/gpt5-benchmarks", "GPT-5 benchmark results show surprising coding gains"),
        ("/articles/claude-analysis", "Claude 3.7 analysis: where it wins and where it lags"),
        ("/nav", "Home"),  # too short / no article segment -- should be skipped
    ])
    _patch_fetch(monkeypatch, html)
    items = fetch_signals._fetch_html_source("Test Source", "https://example.com/articles")
    assert len(items) == 2
    assert all(i["source"] == "Test Source" for i in items)
    assert all(i["link"].startswith("http") for i in items)


def test_html_source_dedupes(monkeypatch):
    import fetch_signals
    title = "Repeated article title that is long enough to pass the filter"
    html = _make_html([
        ("/articles/one", title),
        ("/articles/two", title),
    ])
    _patch_fetch(monkeypatch, html)
    items = fetch_signals._fetch_html_source("Dup Source", "https://example.com/articles")
    assert len(items) == 1


def test_html_source_fetch_error(monkeypatch):
    import fetch_signals
    def _fail(url, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(fetch_signals, "_fetch_url", _fail)
    items = fetch_signals._fetch_html_source("Bad Source", "https://example.com/articles")
    assert items == []


def test_fetch_feed_dispatches_html(monkeypatch):
    import fetch_signals
    html = _make_html([
        ("/articles/story", "A long enough article title about some AI benchmark comparison"),
    ])
    _patch_fetch(monkeypatch, html)
    items = fetch_signals.fetch_feed("AA", "https://example.com/articles", feed_type="html")
    assert len(items) == 1


def test_load_feeds_preserves_type():
    import fetch_signals
    feeds = fetch_signals._load_feeds()
    # At least one entry should be a dict (the new format)
    assert all(isinstance(f, dict) for f in feeds), "All feeds should be dicts"
    labels = [f["label"] for f in feeds]
    assert "Artificial Analysis" in labels
    aa = next(f for f in feeds if f["label"] == "Artificial Analysis")
    assert aa.get("type") == "html"
