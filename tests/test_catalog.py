from app.services.catalog import load_bundled_catalog, load_catalog, source_kind, source_label


def test_catalog_source_defaults_to_rss():
    assert source_kind({}) == "rss"
    assert source_label({"url": "https://example.com/rss.xml"}) == "RSS"


def test_catalog_source_webpage_is_scrape():
    item = {"type": "webpage", "url": "https://hackaday.com/"}
    assert source_kind(item) == "webpage"
    assert source_label(item) == "Scrape"


def test_catalog_includes_techcrunch_rss():
    item = next(entry for entry in load_catalog() if entry["id"] == "techcrunch")
    assert item["url"] == "https://techcrunch.com/feed/"
    assert source_kind(item) == "rss"
    assert item["category"] == "technology"


def test_catalog_ids_and_urls_are_unique():
    items = load_bundled_catalog()
    ids = [item["id"] for item in items]
    urls = [item["url"] for item in items]
    assert len(ids) == len(set(ids))
    assert len(urls) == len(set(urls))
    assert len(items) >= 75


def test_catalog_has_culture_library():
    culture = [item for item in load_catalog() if item["category"] == "culture"]
    ids = {item["id"] for item in culture}
    assert {"guardian-culture", "hyperallergic", "lithub", "pitchfork"} <= ids


def test_catalog_has_nordic_sources_that_translate():
    nordic = [item for item in load_catalog() if item["category"] == "nordic"]
    ids = {item["id"] for item in nordic}
    assert {"dr-nyheder", "politiken", "nrk", "svt-nyheter", "yle", "dr-copenhagen", "tv2-kosmopol"} <= ids
    english = {"copenhagen-post", "the-local-dk"}
    assert english <= ids
    assert all(item.get("translate") is True for item in nordic if item["id"] not in english)
    assert all(not item.get("translate") for item in nordic if item["id"] in english)


def test_catalog_has_australian_library():
    australia = [item for item in load_catalog() if item["category"] == "australia"]
    ids = {item["id"] for item in australia}
    assert {"abc-news-au", "guardian-australia", "sbs-news", "smh", "the-age", "afr", "abc-sport-au", "itnews"} <= ids
    assert all(item["category"] == "australia" for item in australia)


def test_remove_recommended_deletes_enabled_feed():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Feed
    from app.routers.feeds import add_catalog_feed, remove_recommended
    from app.services.catalog import catalog_with_status

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    add_catalog_feed(db, "techcrunch")
    assert any(item["id"] == "techcrunch" and item["added"] for item in catalog_with_status(db))
    assert remove_recommended("techcrunch", db) == {"ok": True, "removed": True}
    assert db.query(Feed).filter(Feed.catalog_id == "techcrunch").one_or_none() is None
    assert any(item["id"] == "techcrunch" and not item["added"] for item in catalog_with_status(db))
