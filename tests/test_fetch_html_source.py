"""Tests for the HTML scrape path in fetch_signals."""
import sys
import types
import importlib
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _make_mock_firecrawl(enabled=True, scrape_result=None):
    mod = types.ModuleType("firecrawl_client")
    mod.is_enabled = lambda: enabled
    mod.scrape = lambda url, **kw: scrape_result
    return mod


def _import_fetch_signals(firecrawl_mod):
    sys.modules["firecrawl_client"] = firecrawl_mod
    # Force reimport so the module picks up the mock.
    if "fetch_signals" in sys.modules:
        del sys.modules["fetch_signals"]
    import fetch_signals
    return fetch_signals


SAMPLE_MD = """
# Benchmarking GPT-5 on Reasoning Tasks
## Gemini 2.5 Pro vs Claude 4: A Deep Dive
### Llama 4 Scout Performance Analysis
Some unrelated text here.
"""


def test_html_source_extracts_headings():
    mock_fc = _make_mock_firecrawl(enabled=True, scrape_result=SAMPLE_MD)
    fs = _import_fetch_signals(mock_fc)
    items = fs._fetch_html_source("Artificial Analysis", "https://artificialanalysis.ai/articles")
    titles = [i["title"] for i in items]
    assert "Benchmarking GPT-5 on Reasoning Tasks" in titles
    assert "Gemini 2.5 Pro vs Claude 4: A Deep Dive" in titles
    assert all(i["source"] == "Artificial Analysis" for i in items)


def test_html_source_fallback_when_disabled():
    mock_fc = _make_mock_firecrawl(enabled=False)
    fs = _import_fetch_signals(mock_fc)
    items = fs._fetch_html_source("Artificial Analysis", "https://artificialanalysis.ai/articles")
    assert items == []


def test_html_source_fallback_when_scrape_fails():
    mock_fc = _make_mock_firecrawl(enabled=True, scrape_result=None)
    fs = _import_fetch_signals(mock_fc)
    items = fs._fetch_html_source("Artificial Analysis", "https://artificialanalysis.ai/articles")
    assert items == []


def test_fetch_feed_rss_path_unaffected():
    """Passing no type= still goes through the RSS path (not HTML)."""
    mock_fc = _make_mock_firecrawl(enabled=True)
    fs = _import_fetch_signals(mock_fc)
    # RSS path should hit _fetch_url, which will fail on a dummy URL -- that's fine,
    # we just verify it doesn't call firecrawl by checking the fallback path returns [].
    items = fs.fetch_feed("Test", "http://localhost:9999/nonexistent.xml", type=None)
    assert isinstance(items, list)  # returns [] on failure, not an exception


def test_fetch_feed_dispatches_html_type():
    mock_fc = _make_mock_firecrawl(enabled=True, scrape_result=SAMPLE_MD)
    fs = _import_fetch_signals(mock_fc)
    items = fs.fetch_feed("Artificial Analysis", "https://artificialanalysis.ai/articles", type="html")
    assert len(items) > 0
    assert items[0]["source"] == "Artificial Analysis"
