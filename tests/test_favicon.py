from pathlib import Path

from app.services import favicon


def test_host_key_strips_www():
    assert favicon.host_key("https://www.bbc.com/news") == "bbc.com"
    assert favicon.host_key("bbc.com") == "bbc.com"


def test_homepage_url_strips_feed_subdomain():
    assert favicon.homepage_url("https://feeds.bbci.co.uk/news/rss.xml") == "https://bbci.co.uk"
    assert favicon.homepage_url("https://www.theguardian.com/world/rss") == "https://theguardian.com"


def test_website_candidates_use_public_bbc_site():
    found = favicon.website_candidates("https://feeds.bbci.co.uk/news/rss.xml")
    assert "https://www.bbc.com" in found
    assert "https://www.bbc.co.uk" in found
    assert "https://bbci.co.uk" not in found


def test_hints_from_feed_use_channel_link_not_banner():
    xml = """<?xml version="1.0"?>
    <rss><channel>
      <title>BBC News</title>
      <link>https://www.bbc.co.uk/news/</link>
      <image><url>https://news.bbcimg.co.uk/nol/shared/img/bbc_news_120x60.gif</url></image>
    </channel></rss>"""
    icons, sites = favicon._hints_from_feed(xml, "https://feeds.bbci.co.uk/news/rss.xml")
    assert icons == []
    assert sites[0] == "https://www.bbc.co.uk/news/"


def test_hints_from_feed_keeps_icon_url():
    xml = """<rss><channel>
      <link>https://example.com/</link>
      <image><url>https://example.com/favicon.png</url></image>
    </channel></rss>"""
    icons, sites = favicon._hints_from_feed(xml, "https://example.com/rss.xml")
    assert "https://example.com/favicon.png" in icons
    assert sites[0] == "https://example.com/"


def test_icon_candidates_from_html():
    html = '<link rel="icon" href="/graphics/favicon.png">'
    found = favicon._icon_candidates("https://example.com/page", html)
    assert "https://example.com/graphics/favicon.png" in found
    assert "https://example.com/favicon.ico" in found


def test_ext_for_png_and_ico():
    assert favicon._ext_for(b"\x89PNG\r\n\x1a\nrest", "", "") == ".png"
    assert favicon._ext_for(b"\x00\x00\x01\x00rest", "", "") == ".ico"
    assert favicon._ext_for(b"not-an-image", "text/html", "") is None


STUB_ICO = (
    b"\x00\x00\x01\x00\x01\x00\x10\x10\x02\x00\x01\x00\x01\x00\xb0\x00\x00\x00"
    b"\x16\x00\x00\x00" + b"\x00" * 176
)


def test_stub_favicon_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    stub = tmp_path / "example.com.ico"
    stub.write_bytes(STUB_ICO)
    assert favicon._is_stub_icon(STUB_ICO)
    assert favicon.cached_src("example.com.ico") is None
    assert favicon.stored_path("https://example.com/feed") is None


def test_seed_bundled_favicons_fills_empty_cache(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    runtime = tmp_path / "runtime"
    bundled.mkdir()
    runtime.mkdir()
    (bundled / "site.com.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (bundled / "blank.com.ico").write_bytes(STUB_ICO)
    monkeypatch.setattr(favicon, "BUNDLED_FAVICON_DIR", bundled)
    monkeypatch.setattr(favicon, "FAVICON_DIR", runtime)
    favicon.seed_bundled_favicons()
    assert (runtime / "site.com.png").is_file()
    assert not (runtime / "blank.com.ico").exists()
    (runtime / "site.com.png").write_bytes(b"\x89PNG\r\n\x1a\nkept")
    favicon.seed_bundled_favicons()
    assert (runtime / "site.com.png").read_bytes() == b"\x89PNG\r\n\x1a\nkept"


def test_cached_src_and_ensure_uses_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    icon = tmp_path / "example.com.png"
    icon.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def fail_get(*_args, **_kwargs):
        raise AssertionError("should not fetch when a cached icon exists")

    monkeypatch.setattr(favicon, "_get", fail_get)
    path = favicon.ensure_favicon("https://example.com/feed")
    assert path == icon
    assert favicon.cached_src("example.com.png") == "/favicons/example.com.png"
    assert favicon.stored_path("https://www.example.com/story") == icon


class FakeFeed:
    def __init__(self, name, url, catalog_id=None, favicon_name=None):
        self.name = name
        self.url = url
        self.catalog_id = catalog_id
        self.favicon_name = favicon_name


def test_map_and_lookup_use_cached_name(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "bbc.com.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    mapping = favicon.map_for_feeds(
        [FakeFeed("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "bbc-world", "bbc.com.png")]
    )
    assert mapping["BBC World"] == "/favicons/bbc.com.png"
    assert favicon.lookup(mapping, "Other", "BBC World") == "/favicons/bbc.com.png"
    assert favicon.lookup(mapping, "missing") is None


class FakeStory:
    def __init__(self, source_name, canonical_url):
        self.source_name = source_name
        self.canonical_url = canonical_url


def test_map_and_lookup_saved_article_url(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "en.wikipedia.org.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    url = "https://en.wikipedia.org/wiki/Example"
    mapping = favicon.map_for_stories([FakeStory("en.wikipedia.org", url)])
    assert mapping["en.wikipedia.org"] == "/favicons/en.wikipedia.org.png"
    assert favicon.src_for_url(url) == "/favicons/en.wikipedia.org.png"
    assert favicon.lookup({}, url) == "/favicons/en.wikipedia.org.png"


def test_fetch_icon_falls_back_to_website(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, url, content, content_type):
            self.url = url
            self.content = content
            self.text = content.decode("utf-8", errors="ignore")
            self.status_code = 200
            self.headers = {"content-type": content_type}

    def fake_get(url, accept, client=None):
        calls.append(url)
        if url.endswith("rss.xml"):
            body = b"<rss><channel><link>https://www.bbc.co.uk/news/</link></channel></rss>"
            return FakeResponse(url, body, "application/rss+xml")
        if "bbc.co.uk/news" in url or url.rstrip("/") in {"https://www.bbc.com", "https://www.bbc.co.uk"}:
            body = b'<html><link rel="icon" href="/favicon.png"></html>'
            return FakeResponse(url, body, "text/html")
        if "bbci.co.uk" in url and url.endswith("/favicon.ico"):
            return None
        if url.endswith("/favicon.png") or url.endswith("/favicon.ico"):
            return FakeResponse(url, b"\x89PNG\r\n\x1a\nfake", "image/png")
        return None

    monkeypatch.setattr(favicon, "_get", fake_get)
    path = favicon._fetch_icon("https://feeds.bbci.co.uk/news/rss.xml")
    assert path is not None
    assert path.exists()
    assert any("bbc.co.uk" in item or "bbc.com" in item for item in calls if "favicon" in item)


def test_fetch_skips_blank_ico_for_homepage_png(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)

    class FakeResponse:
        def __init__(self, url, content, content_type):
            self.url = url
            self.content = content
            self.text = content.decode("utf-8", errors="ignore")
            self.status_code = 200
            self.headers = {"content-type": content_type}

    def fake_get(url, accept, client=None):
        if url.endswith("/feed/"):
            return FakeResponse(url, b"<rss><channel><link>https://techcrunch.com/</link></channel></rss>", "application/rss+xml")
        if url.rstrip("/") == "https://techcrunch.com":
            return FakeResponse(
                url,
                b'<html><link rel="icon" href="/wp-content/favicon.png"></html>',
                "text/html",
            )
        if url.endswith("favicon.ico"):
            return FakeResponse(url, STUB_ICO, "image/x-icon")
        if url.endswith(".png"):
            return FakeResponse(url, b"\x89PNG\r\n\x1a\nreal", "image/png")
        return None

    monkeypatch.setattr(favicon, "_get", fake_get)
    path = favicon._fetch_icon("https://techcrunch.com/feed/")
    assert path is not None
    assert path.suffix == ".png"
    assert path.read_bytes().startswith(b"\x89PNG")


def test_fetch_uses_helper_when_site_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)

    class FakeResponse:
        def __init__(self, url, content, content_type):
            self.url = url
            self.content = content
            self.text = content.decode("utf-8", errors="ignore")
            self.status_code = 200
            self.headers = {"content-type": content_type}

    def fake_get(url, accept, client=None):
        if "duckduckgo.com" in url or "google.com" in url:
            return FakeResponse(url, b"\x89PNG\r\n\x1a\nhelper", "image/png")
        return None

    monkeypatch.setattr(favicon, "_get", fake_get)
    path = favicon._fetch_icon("https://feeds.apnews.com/apf-topnews")
    assert path is not None
    assert path.read_bytes().startswith(b"\x89PNG")


def test_capture_for_feed_skips_fetch_when_named(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "site.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    feed = FakeFeed("Site", "https://site.example/rss", favicon_name="site.png")

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when favicon_name is already cached")

    monkeypatch.setattr(favicon, "_fetch_icon", fail_fetch)
    assert favicon.capture_for_feed(feed) == "site.png"


def _feed_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_backfill_links_disk_without_fetch(tmp_path, monkeypatch):
    from app.models import Feed

    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "example.com.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    db = _feed_session()
    feed = Feed(name="Example", url="https://example.com/rss", enabled=True)
    db.add(feed)
    db.commit()

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when disk icon exists")

    monkeypatch.setattr(favicon, "_fetch_icon", fail_fetch)
    result = favicon.backfill_missing_feeds(db)
    db.refresh(feed)
    assert feed.favicon_name == "example.com.png"
    assert result["linked"] == 1
    assert result["fetched"] == 0


def test_backfill_skips_named_and_disabled(tmp_path, monkeypatch):
    from app.models import Feed

    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "named.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    db = _feed_session()
    db.add(Feed(name="Named", url="https://named.example/rss", enabled=True, favicon_name="named.png"))
    db.add(Feed(name="Off", url="https://off.example/rss", enabled=False))
    db.commit()
    fetches: list[str] = []

    def track_fetch(url):
        fetches.append(url)
        return None

    monkeypatch.setattr(favicon, "_fetch_icon", track_fetch)
    result = favicon.backfill_missing_feeds(db)
    assert fetches == []
    assert result["fetched"] == 0


def test_backfill_respects_attempt_cooldown(tmp_path, monkeypatch):
    from app.models import Feed

    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    db = _feed_session()
    db.add(Feed(name="Missing", url="https://missing.example/rss", enabled=True))
    db.commit()
    favicon._mark_attempt("https://missing.example/rss")
    fetches: list[str] = []

    def track_fetch(url):
        fetches.append(url)
        return None

    monkeypatch.setattr(favicon, "_fetch_icon", track_fetch)
    result = favicon.backfill_missing_feeds(db)
    assert fetches == []
    assert result["skipped"] >= 1

