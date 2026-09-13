from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed
from app.services.catalog import load_catalog
from app.services.packages import (
    export_category,
    import_package,
    package_catalog_id,
    validate_package,
)


SAMPLE = {
    "format": "newscast-package",
    "format_version": 1,
    "id": "romania",
    "name": "Romania",
    "version": "1.0.0",
    "category": {"key": "romania", "label": "Romania"},
    "feeds": [
        {
            "id": "hotnews",
            "name": "HotNews",
            "url": "https://hotnews.example/rss",
            "type": "rss",
            "translate": True,
            "default_enabled": False,
        }
    ],
}


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_validate_and_prefix_id():
    package = validate_package(SAMPLE)
    assert package["id"] == "romania"
    assert package_catalog_id(package["id"], package["feeds"][0]["id"]) == "romania.hotnews"


def test_validate_rejects_bad_url():
    bad = {**SAMPLE, "feeds": [{"id": "x", "name": "X", "url": "ftp://nope"}]}
    try:
        validate_package(bad)
        raise AssertionError("expected bad url to fail")
    except ValueError:
        pass


def test_import_creates_category_and_feed(tmp_path, monkeypatch):
    from app.services import packages

    monkeypatch.setattr(packages, "PACKAGES_DIR", tmp_path)
    db = _session()
    result = import_package(db, SAMPLE)
    assert result["created"] == 1
    feed = db.query(Feed).one()
    assert feed.catalog_id == "romania.hotnews"
    assert feed.category == "romania"
    assert feed.enabled is False
    assert feed.translate is True
    again = import_package(db, SAMPLE)
    assert again["created"] == 0


def test_load_catalog_includes_imported_package(tmp_path, monkeypatch):
    from app.services import packages

    monkeypatch.setattr(packages, "PACKAGES_DIR", tmp_path)
    db = _session()
    import_package(db, SAMPLE)
    ids = {item["id"] for item in load_catalog()}
    assert "romania.hotnews" in ids


def test_export_category_from_feeds():
    db = _session()
    db.add(
        Feed(
            name="HotNews",
            url="https://hotnews.example/rss",
            category="romania",
            type="rss",
            translate=True,
        )
    )
    db.commit()
    package = export_category(db, "romania", [])
    assert package["id"] == "romania"
    assert package["feeds"][0]["url"] == "https://hotnews.example/rss"
