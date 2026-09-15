from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, LibraryFile, User
from app.routers import opds as opds_router
from app.services import hostname, opds, passwords, settings, user_settings


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
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname.env, "public_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "")
    monkeypatch.setattr(settings.env, "x3_sync_token", "")
    monkeypatch.setattr(settings.env, "x3_catalog_login", "")
    monkeypatch.setattr(settings.env, "x3_catalog_username", "")
    monkeypatch.setattr(settings.env, "instance_name", "")
    monkeypatch.setattr(settings.env, "device_hostname", "")


def test_user_a_token_cannot_read_user_b_opds(monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    settings.set_value(db, "x3_catalog_login", "1")
    alice = User(username="alice", password=passwords.hash_password("aaaa"), role="user", active=True)
    bob = User(username="bob", password=passwords.hash_password("bbbb"), role="user", active=True)
    db.add_all([alice, bob])
    db.commit()
    db.refresh(alice)
    db.refresh(bob)
    user_settings.set_value(db, alice.id, "x3_sync_token", "alice-secret")
    user_settings.set_value(db, bob.id, "x3_sync_token", "bob-secret")
    db.add(
        LibraryFile(
            user_id=bob.id,
            title="Bob private",
            original_name="bob.epub",
            stored_name="bob.epub",
            size=4,
            created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
    )
    db.commit()
    client = _client(db)

    denied = client.get("/opds/u/bob/", auth=("bob", "alice-secret"))
    assert denied.status_code == 401

    ok = client.get("/opds/u/bob/library", auth=("bob", "bob-secret"))
    assert ok.status_code == 200
    assert "Bob private" in ok.text

    alice_view = client.get("/opds/u/alice/library", auth=("alice", "alice-secret"))
    assert alice_view.status_code == 200
    assert "Bob private" not in alice_view.text


def test_per_user_briefing_paths_in_feed(tmp_path, monkeypatch):
    db = _session()
    _prep(monkeypatch, db)
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    from datetime import date

    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 14))
    user = User(username="dana", password=passwords.hash_password("dddd"), role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    root = tmp_path / str(user.id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "news-2026-09-14.epub").write_bytes(b"PK")
    xml = opds.briefing_feed(db, user_id=user.id, username="dana")
    assert "/api/x3/u/dana/papers/2026-09-14/" in xml
