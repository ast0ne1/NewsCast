from app.models import Feed
from app.services.ingest import _collect_feed_items


RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Automattic and WordPress headlines</title>
      <link>https://techcrunch.com/2026/09/12/automattic/</link>
    </item>
  </channel>
</rss>
"""


def test_webpage_uses_common_feed_before_homepage(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> str:
        calls.append(url)
        if url.rstrip("/").endswith("/feed"):
            return RSS
        raise AssertionError(f"homepage should not be fetched first: {url}")

    monkeypatch.setattr("app.services.ingest._http_get", fake_get)
    feed = Feed(name="TechCrunch", url="https://techcrunch.com/", type="webpage")
    items, status = _collect_feed_items(feed)
    assert calls == ["https://techcrunch.com/feed/"]
    assert status == 200
    assert items[0]["title"] == "Automattic and WordPress headlines"
    assert items[0]["url"] == "https://techcrunch.com/2026/09/12/automattic"
