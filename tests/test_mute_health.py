from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, utcnow
from app.services.health import feed_health, record_fetch
from app.services.ingest import run_ingest
from app.services.schedule import feed_is_muted


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_mute_window():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    feed = Feed(name="BBC", url="https://example.com/rss", muted_until=now + timedelta(hours=2))
    assert feed_is_muted(feed, now) is True
    assert feed_is_muted(feed, now + timedelta(hours=3)) is False


def test_ingest_skips_muted_unless_single_feed(monkeypatch):
    db = _session()
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    muted = Feed(
        name="Muted",
        url="https://example.com/muted",
        enabled=True,
        type="rss",
        muted_until=utcnow() + timedelta(hours=24),
    )
    open_feed = Feed(name="Open", url="https://example.com/open", enabled=True, type="rss")
    db.add_all([muted, open_feed])
    db.commit()
    collected: list[str] = []

    def fake_collect(feed):
        collected.append(feed.name)
        return ([{"title": "A", "url": f"https://example.com/{feed.name}", "excerpt": "x", "published_at": now, "source": feed.name}], 200)

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    monkeypatch.setattr("app.services.briefing.enqueue_latest_briefing", lambda _db: None)
    monkeypatch.setattr("app.services.briefing.purge_expired_stories", lambda _db: None)

    run_ingest(db, force=True)
    assert collected == ["Open"]

    collected.clear()
    run_ingest(db, force=True, feed_id=muted.id)
    assert collected == ["Muted"]


def test_404_is_error_and_empty_week_badge():
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    errored = Feed(name="Gone", url="https://example.com/404", last_status_code=404, last_item_count=0)
    empty = Feed(
        name="Quiet",
        url="https://example.com/empty",
        last_item_count=0,
        empty_since=now - timedelta(days=8),
    )
    healthy = Feed(name="Ok", url="https://example.com/ok", last_status_code=200, last_item_count=4)
    assert feed_health(errored, now) == "error"
    assert feed_health(empty, now) == "empty"
    assert feed_health(healthy, now) == "ok"


def test_record_fetch_clears_empty_since():
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    feed = Feed(name="Ok", url="https://example.com/ok", empty_since=now - timedelta(days=3), last_item_count=0)
    record_fetch(feed, status_code=200, item_count=3, now=now)
    assert feed.empty_since is None
    assert feed.last_item_count == 3
