from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, LibraryFile
from app.routers import opds as opds_router
from app.services import hostname, opds, settings


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
    app.include_router(opds_router.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _prep(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(hostname.env, "public_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings.env, "x3_sync_token", "")
    monkeypatch.setattr(settings.env, "x3_catalog_login", "")
    monkeypatch.setattr(settings.env, "x3_catalog_username", "")
    monkeypatch.setattr(settings.env, "instance_name", "")
    monkeypatch.setattr(settings.env, "device_hostname", "")


def test_briefing_entry_title_uses_instance_and_date():
    when = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)
    assert opds.briefing_entry_title("Work", when) == "NewsCast · Work — 13 Sep 2026"
    assert opds.briefing_entry_title("", when) == "NewsCast briefing — 13 Sep 2026"


def test_briefing_feed_has_epub_acquisition(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    settings.set_value(db, "instance_name", "Work")
    xml = opds.briefing_feed(db)
    assert "NewsCast · Work —" in xml
    assert 'type="application/epub+zip"' in xml
    assert 'rel="http://opds-spec.org/acquisition"' in xml
    assert "http://127.0.0.1:8080/api/x3/news.epub" in xml


def test_library_lists_uploaded_file(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    db.add(
        LibraryFile(
            title="Notes from a meeting",
            original_name="notes.epub",
            stored_name="20260913-notes.epub",
            size=12,
            created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
    )
    db.commit()
    xml = opds.library_feed(db)
    assert "Notes from a meeting" in xml
    assert "http://127.0.0.1:8080/api/v1/files/20260913-notes.epub" in xml
    assert 'type="application/epub+zip"' in xml


def test_opds_open_without_token(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    client = _client(db)
    response = client.get("/opds")
    assert response.status_code == 200
    assert "Today's briefing" in response.text
    assert "/opds/briefing" in response.text
    assert "profile=opds-catalog" in response.headers["content-type"]


def test_opds_stays_open_when_catalog_login_off(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    settings.set_value(db, "x3_sync_token", "secret-token")
    settings.set_value(db, "x3_catalog_login", "0")
    client = _client(db)
    response = client.get("/opds")
    assert response.status_code == 200
    assert "Today's briefing" in response.text


def test_opds_requires_basic_when_token_set(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    settings.set_value(db, "x3_sync_token", "secret-token")
    settings.set_value(db, "x3_catalog_login", "1")
    client = _client(db)

    bare = client.get("/opds")
    assert bare.status_code == 401
    assert bare.headers["www-authenticate"] == 'Basic realm="NewsCast"'

    wrong = client.get("/opds", auth=("newscast", "nope"))
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == 'Basic realm="NewsCast"'

    other_user = client.get("/opds", auth=("admin", "secret-token"))
    assert other_user.status_code == 401

    ok = client.get("/opds", auth=("newscast", "secret-token"))
    assert ok.status_code == 200
    assert "Today's briefing" in ok.text


def test_opds_uses_configured_username(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    settings.set_value(db, "x3_sync_token", "secret-token")
    settings.set_value(db, "x3_catalog_login", "1")
    settings.set_value(db, "x3_catalog_username", "Work")
    client = _client(db)

    default_user = client.get("/opds", auth=("newscast", "secret-token"))
    assert default_user.status_code == 401

    ok = client.get("/opds", auth=("Work", "secret-token"))
    assert ok.status_code == 200
    assert "Today's briefing" in ok.text
