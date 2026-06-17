import json
import pathlib

def test_together_feed_present():
    feeds_path = pathlib.Path('data/feeds.json')
    data = json.loads(feeds_path.read_text())
    urls = [f['url'] for f in data['feeds']]
    assert 'https://www.together.ai/blog/rss.xml' in urls, 'Together AI feed missing from data/feeds.json'
