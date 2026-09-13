from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import search_stories


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_search_hits_title_and_starred(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add_all(
        [
            Story(
                title="Climate talks reopen",
                summary="Diplomats meet",
                source_name="BBC World",
                canonical_url="https://example.com/climate",
                content_hash="1",
                cluster_key="1",
                raw_excerpt="climate",
                published_at=now,
                created_at=now,
            ),
            Story(
                title="Old favourite",
                summary="Kept",
                source_name="Guardian",
                canonical_url="https://example.com/old",
                content_hash="2",
                cluster_key="2",
                raw_excerpt="",
                favourited=True,
                published_at=now - timedelta(days=30),
                created_at=now - timedelta(days=30),
            ),
            Story(
                title="Unrelated",
                summary="Sport",
                source_name="BBC Sport",
                canonical_url="https://example.com/sport",
                content_hash="3",
                cluster_key="3",
                published_at=now,
                created_at=now,
            ),
        ]
    )
    db.commit()
    titles = [story.title for story in search_stories(db, "climate")]
    assert titles == ["Climate talks reopen"]
    starred = [story.title for story in search_stories(db, "favourite")]
    assert starred == ["Old favourite"]
    assert search_stories(db, "") == []
