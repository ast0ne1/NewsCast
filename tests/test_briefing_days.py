from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import current_stories, normalize_briefing_day


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_normalize_briefing_day():
    assert normalize_briefing_day(None) == "today"
    assert normalize_briefing_day("Yesterday") == "yesterday"
    assert normalize_briefing_day("2026-09-13") == "2026-09-13"
    assert normalize_briefing_day("nope") == "today"


def test_today_vs_yesterday_selection(monkeypatch):
    now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 13))
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add_all(
        [
            Story(
                title="Today story",
                summary="Today",
                source_name="BBC",
                canonical_url="https://example.com/today",
                content_hash="t",
                cluster_key="t",
                published_at=now,
                created_at=now,
                importance=3,
            ),
            Story(
                title="Yesterday story",
                summary="Yesterday",
                source_name="BBC",
                canonical_url="https://example.com/yesterday",
                content_hash="y",
                cluster_key="y",
                published_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                importance=3,
            ),
            Story(
                title="Saved long-read",
                summary="Keep",
                source_name="Saved",
                canonical_url="https://example.com/saved",
                content_hash="s",
                cluster_key="s",
                saved=True,
                published_at=now - timedelta(days=3),
                created_at=now - timedelta(days=3),
                importance=3,
            ),
        ]
    )
    db.commit()
    today = current_stories(db, day="today")
    yesterday = current_stories(db, day="yesterday")
    assert [story.title for story in today] == ["Saved long-read", "Today story"]
    assert [story.title for story in yesterday] == ["Yesterday story"]
