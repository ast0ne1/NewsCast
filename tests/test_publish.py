from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, Story
from app.routers import x3 as x3_router
from app.services import settings
from app.services.briefing import (
    maybe_publish_daily_briefing,
    paper_status,
    prune_old_briefings,
    publish_daily_briefing,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _client(db: Session) -> TestClient:
    app = FastAPI()
    app.include_router(x3_router.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _seed_story(db: Session) -> None:
    when = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
    db.add(
        Story(
            title="Morning headline",
            summary="A short summary.",
            source_name="BBC World",
            canonical_url="https://example.com/morning",
            content_hash="m",
            cluster_key="m",
            published_at=when,
            created_at=when,
            importance=3,
        )
    )
    db.commit()


def _freeze_briefing_clock(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc))
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)


def test_publish_writes_dated_file_once(tmp_path: Path, monkeypatch):
    _freeze_briefing_clock(monkeypatch, tmp_path)
    db = _session()
    _seed_story(db)
    now = datetime(2026, 9, 14, 7, 0)
    path = publish_daily_briefing(db, now=now)
    assert path.name == "news-2026-09-14.epub"
    assert path.exists()
    first = path.read_bytes()
    path.write_bytes(b"frozen-once")
    again = publish_daily_briefing(db, now=now)
    assert again.read_bytes() == b"frozen-once"
    assert first != b"frozen-once"


def test_maybe_publish_waits_until_publish_at(tmp_path: Path, monkeypatch):
    _freeze_briefing_clock(monkeypatch, tmp_path)
    db = _session()
    _seed_story(db)
    settings.set_value(db, "briefing_publish_at", "06:30")
    assert maybe_publish_daily_briefing(db, now=datetime(2026, 9, 14, 6, 0)) is None
    assert not (tmp_path / "1" / "news-2026-09-14.epub").exists()
    path = maybe_publish_daily_briefing(db, now=datetime(2026, 9, 14, 6, 30))
    assert path is not None
    assert path.exists()
    assert maybe_publish_daily_briefing(db, now=datetime(2026, 9, 14, 8, 0)) is None


def test_prune_keeps_seven_dated_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    root = tmp_path / "1"
    root.mkdir(parents=True, exist_ok=True)
    today = date(2026, 9, 14)
    for offset in range(8):
        day = today - timedelta(days=offset)
        (root / f"news-{day.isoformat()}.epub").write_bytes(b"x")
        (root / f"news-{day.isoformat()}.txt").write_text("x", encoding="utf-8")
    assert prune_old_briefings(keep=7) == 1
    remaining = {path.name for path in root.glob("*.epub")}
    expected = {f"news-{(today - timedelta(days=offset)).isoformat()}.epub" for offset in range(7)}
    assert remaining == expected


def test_x3_serves_frozen_file_not_live_rebuild(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "x3_catalog_login", "0")
    frozen = tmp_path / "1" / "news-2026-09-14.epub"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_bytes(b"PK frozen-paper")
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    client = _client(db)
    response = client.get("/api/x3/news.epub")
    assert response.status_code == 200
    assert response.content == b"PK frozen-paper"


def test_x3_serves_iso_day_with_dated_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "x3_catalog_login", "0")
    target = tmp_path / "1" / "news-2026-09-13.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK yesterday-paper")
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    client = _client(db)
    response = client.get("/api/x3/news.epub?day=2026-09-13")
    assert response.status_code == 200
    assert response.content == b"PK yesterday-paper"
    assert "NewsCast%20-%202026-09-13.epub" in response.headers.get("content-disposition", "")


def test_x3_missing_paper_is_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "x3_catalog_login", "0")
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    client = _client(db)
    response = client.get("/api/x3/news.epub")
    assert response.status_code == 404
    assert "not published" in response.json()["detail"]


def test_x3_named_category_url_sets_category_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "x3_catalog_login", "0")
    settings.set_value(db, "reader_title_pattern", "NewsCast {date}")
    settings.set_value(db, "reader_category_title_pattern", "NewsCast {category} {date}")
    settings.set_value(db, "reader_date_format", "iso")
    target = tmp_path / "1" / "news-2026-09-14-technology.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK tech-paper")
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    client = _client(db)
    response = client.get("/api/x3/papers/2026-09-14/category/technology/NewsCast%20Tech%202026-09-14.epub")
    assert response.status_code == 200
    assert response.content == b"PK tech-paper"
    disposition = response.headers.get("content-disposition", "")
    assert "NewsCast%20Tech%202026-09-14.epub" in disposition
    assert "NewsCast%202026-09-14.epub" not in disposition


def test_x3_legacy_category_query_still_works(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "x3_catalog_login", "0")
    settings.set_value(db, "reader_category_title_pattern", "NewsCast {category} {date}")
    settings.set_value(db, "reader_date_format", "iso")
    target = tmp_path / "1" / "news-2026-09-14-technology.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK tech-paper")
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    client = _client(db)
    response = client.get("/api/x3/news.epub?day=2026-09-14&category=technology")
    assert response.status_code == 200
    assert "NewsCast%20Tech%202026-09-14.epub" in response.headers.get("content-disposition", "")


def test_paper_status_message(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    db = _session()
    settings.set_value(db, "briefing_publish_at", "06:30")
    now = datetime(2026, 9, 14, 7, 0)
    status = paper_status(db, now=now)
    assert status["published"] is False
    assert "not ready" in status["message"]
    paper = tmp_path / "1" / "news-2026-09-14.epub"
    paper.parent.mkdir(parents=True, exist_ok=True)
    paper.write_bytes(b"x")
    status = paper_status(db, now=now)
    assert status["published"] is True
    assert "published at 06:30" in status["message"]
