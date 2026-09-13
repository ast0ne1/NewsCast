from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services.ingest import run_ingest, start_ingest


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ingest_one_feed_skips_others(monkeypatch):
    db = _session()
    first = Feed(
        name="DR News",
        url="https://www.dr.dk/nyheder/service/feeds/allenyheder",
        enabled=True,
        type="rss",
        category="nordic",
        summarize=False,
    )
    second = Feed(
        name="BBC World",
        url="https://feeds.bbci.co.uk/news/world/rss.xml",
        enabled=True,
        type="rss",
        category="news",
        summarize=False,
    )
    db.add_all([first, second])
    db.commit()
    collected: list[str] = []

    def fake_collect(feed):
        collected.append(feed.name)
        return [
            {
                "title": f"Story from {feed.name}",
                "url": f"https://example.com/{feed.name.lower().replace(' ', '-')}",
                "excerpt": "A short excerpt for the test.",
                "published_at": datetime(2026, 9, 13, tzinfo=timezone.utc),
                "source": feed.name,
            }
        ]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    monkeypatch.setattr("app.services.briefing.enqueue_latest_briefing", lambda _db: None)
    monkeypatch.setattr("app.services.briefing.purge_expired_stories", lambda _db: None)

    result = run_ingest(db, force=True, feed_id=first.id)
    assert result["ok"] is True
    assert collected == ["DR News"]
    assert "Updated DR News" in result["message"]
    assert db.query(Story).count() == 1
    assert db.query(Story).one().source_name == "DR News"


def test_ingest_one_feed_missing():
    db = _session()
    result = run_ingest(db, force=True, feed_id=999)
    assert result["ok"] is False
    assert result["message"] == "Feed not found."


def test_start_ingest_missing_feed(monkeypatch):
    class FakeSession:
        def get(self, _model, _feed_id):
            return None

        def close(self):
            return None

    monkeypatch.setattr("app.services.ingest.SessionLocal", lambda: FakeSession())
    result = start_ingest(force=True, feed_id=1)
    assert result["ok"] is False
    assert result["running"] is False
    assert result["message"] == "Feed not found."
