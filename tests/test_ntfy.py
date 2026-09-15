from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services import ntfy, reader_push, settings
from app.services.briefing import enqueue_sync_file, publish_daily_briefing


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _enable_publish(db: Session) -> None:
    settings.set_value(db, "ntfy_enabled", "1")
    settings.set_value(db, "ntfy_topic", "newscast-test")
    settings.set_value(db, "ntfy_notify_on_publish", "1")
    settings.set_value(db, "ntfy_server", "https://ntfy.sh")


def _enable_push(db: Session) -> None:
    settings.set_value(db, "ntfy_enabled", "1")
    settings.set_value(db, "ntfy_topic", "newscast-test")
    settings.set_value(db, "ntfy_notify_on_push", "1")
    settings.set_value(db, "ntfy_server", "https://ntfy.sh")


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, content=None, headers=None):
        type(self).posts.append({"url": url, "content": content, "headers": dict(headers or {})})
        return _FakeResponse()


def _patch_httpx(monkeypatch) -> list[dict]:
    _FakeClient.posts = []
    monkeypatch.setattr(ntfy.httpx, "Client", _FakeClient)
    return _FakeClient.posts


def test_notify_skips_when_disabled():
    db = _session()
    settings.set_value(db, "ntfy_topic", "newscast-test")
    settings.set_value(db, "ntfy_notify_on_publish", "1")
    assert ntfy.notify(db, kind="publish", title="T", body="B") is False


def test_notify_skips_empty_topic():
    db = _session()
    settings.set_value(db, "ntfy_enabled", "1")
    settings.set_value(db, "ntfy_notify_on_publish", "1")
    assert ntfy.notify(db, kind="publish", title="T", body="B") is False


def test_notify_skips_when_event_off():
    db = _session()
    settings.set_value(db, "ntfy_enabled", "1")
    settings.set_value(db, "ntfy_topic", "newscast-test")
    assert ntfy.notify(db, kind="publish", title="T", body="B") is False


def test_notify_posts_and_is_idempotent(monkeypatch):
    db = _session()
    _enable_publish(db)
    posts = _patch_httpx(monkeypatch)
    assert ntfy.notify(db, kind="publish", title="Home", body="Morning paper ready") is True
    assert len(posts) == 1
    assert posts[0]["url"] == "https://ntfy.sh/newscast-test"
    assert posts[0]["headers"]["Title"] == "Home"
    assert posts[0]["headers"]["Tags"] == "newspaper"
    assert ntfy.notify(db, kind="publish", title="Home", body="again") is False
    assert len(posts) == 1


def test_publish_daily_briefing_notifies_once(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    monkeypatch.setattr(
        "app.services.briefing.utcnow",
        lambda: datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc),
    )
    db = _session()
    when = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
    db.add(
        Story(
            title="Headline",
            summary="Summary",
            source_name="BBC",
            canonical_url="https://example.com/a",
            content_hash="a",
            cluster_key="a",
            published_at=when,
            created_at=when,
            importance=3,
        )
    )
    db.commit()
    _enable_publish(db)
    posts = _patch_httpx(monkeypatch)
    now = datetime(2026, 9, 15, 7, 0)
    publish_daily_briefing(db, now=now, overwrite=True)
    publish_daily_briefing(db, now=now, overwrite=True)
    assert len(posts) == 1
    assert "newscast-test" in posts[0]["url"]


def test_flush_pending_notifies_push_once(tmp_path: Path, monkeypatch):
    db = _session()
    _enable_push(db)
    day = date.today().isoformat()
    path = tmp_path / f"news-{day}.epub"
    path.write_bytes(b"epub")
    enqueue_sync_file(
        db,
        path,
        path.name,
        kind="crosspoint",
        save_path=f"/News/{path.name}",
    )
    posts = _patch_httpx(monkeypatch)
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host, timeout=None, db=None: True)
    monkeypatch.setattr(reader_push, "upload_file", lambda host, file_path, dest, db=None: None)
    # Re-queue after first flush so a second flush could notify if not idempotent.
    reader_push.flush_pending(db)
    assert len(posts) == 1
    enqueue_sync_file(
        db,
        path,
        path.name,
        kind="crosspoint",
        save_path=f"/News/{path.name}",
    )
    reader_push.flush_pending(db)
    assert len(posts) == 1
