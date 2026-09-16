from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile
import re

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services import opds, settings
from app.services.briefing import publish_daily_briefing
from app.services.categories import seed_builtin_categories


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _epub_title(path: Path) -> str:
    with ZipFile(path) as archive:
        opf = next(name for name in archive.namelist() if name.endswith(".opf"))
        text = archive.read(opf).decode("utf-8", errors="replace")
    match = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", text)
    assert match is not None
    return match.group(1)


def test_category_opds_keys_roundtrip():
    assert settings.parse_category_opds_keys('["news","technology"]') == {"news", "technology"}
    assert settings.encode_category_opds_keys({"technology", "news"}) == '["news","technology"]'
    assert settings.parse_category_opds_keys("") == set()


def test_publish_writes_only_enabled_category_papers(tmp_path: Path, monkeypatch):
    now = datetime(2026, 9, 15, 7, 0)
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 15))
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    monkeypatch.setattr("app.services.briefing.enqueue_latest_briefing", lambda db, **_kwargs: None)
    db = _session()
    seed_builtin_categories(db)
    settings.set_value(db, "briefing_publish_at", "06:30")
    settings.set_value(db, "briefing_category_opds_keys", '["technology"]')
    settings.set_value(db, "reader_paper_label", "My Morning Paper")
    settings.set_value(db, "reader_title_pattern", "{label} - {date}")
    settings.set_value(db, "reader_category_title_pattern", "{category} - {date}")
    settings.set_value(db, "reader_date_format", "dmy")
    db.add_all(
        [
            Feed(name="World Desk", url="https://example.com/world", category="news", enabled=True),
            Feed(name="Tech Desk", url="https://example.com/tech", category="technology", enabled=True),
        ]
    )
    for index in range(3):
        db.add(
            Story(
                title=f"World {index}",
                summary="Summary",
                source_name="World Desk",
                canonical_url=f"https://example.com/world/{index}",
                content_hash=f"world-{index}",
                cluster_key=f"world-{index}",
                published_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
                created_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
                importance=3,
            )
        )
        db.add(
            Story(
                title=f"Tech {index}",
                summary="Summary",
                source_name="Tech Desk",
                canonical_url=f"https://example.com/tech/{index}",
                content_hash=f"tech-{index}",
                cluster_key=f"tech-{index}",
                published_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
                created_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
                importance=3,
            )
        )
    db.commit()

    publish_daily_briefing(db, now=now, overwrite=True)
    main = tmp_path / "1" / "news-2026-09-15.epub"
    tech = tmp_path / "1" / "news-2026-09-15-technology.epub"
    assert main.exists()
    assert tech.exists()
    assert not (tmp_path / "1" / "news-2026-09-15-news.epub").exists()
    assert _epub_title(main) == "My Morning Paper - 15-09-2026"
    assert _epub_title(tech) == "Tech - 15-09-2026"
    assert "·" not in _epub_title(tech)


def test_opds_lists_categories_section(tmp_path: Path, monkeypatch):
    db = _session()
    seed_builtin_categories(db)
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 15))
    monkeypatch.setattr("app.services.hostname.env", settings.env)
    monkeypatch.setattr("app.services.hostname.get_lan_ip", lambda: "")
    monkeypatch.setattr(settings.env, "public_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings.env, "port", 8080)
    settings.set_value(db, "briefing_category_opds_keys", '["technology","news"]')
    root = tmp_path / "1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "news-2026-09-15-technology.epub").write_bytes(b"PK tech")
    (root / "news-2026-09-15-news.epub").write_bytes(b"PK news")

    root = opds.navigation_feed(db)
    assert "/opds/categories" in root
    assert "Categories" in root

    cats = opds.categories_feed(db)
    assert "/opds/categories/technology" in cats
    assert "/opds/categories/news" in cats

    tech = opds.category_feed(db, "technology")
    assert "/api/x3/papers/2026-09-15/category/technology/" in tech
    assert "2026-09-15" in tech
    assert "news.epub?" not in tech
    assert 'title="NewsCast' in tech or "NewsCast -" in tech
