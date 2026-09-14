from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services import settings
from app.services.briefing import current_stories


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_briefing_limit_defaults_and_clamps():
    db = _session()
    settings.set_value(db, "briefing_limit", "40")
    assert settings.briefing_limit(db) == 40
    settings.set_value(db, "briefing_limit", "7")
    assert settings.briefing_limit(db) == settings.DEFAULT_BRIEFING_LIMIT
    settings.set_value(db, "briefing_limit", "nope")
    assert settings.briefing_limit(db) == settings.DEFAULT_BRIEFING_LIMIT


def test_current_stories_uses_saved_briefing_limit(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    settings.set_value(db, "briefing_limit", "10")
    for index in range(15):
        db.add(
            Story(
                title=f"Story {index}",
                summary="Summary",
                source_name="BBC World",
                canonical_url=f"https://example.com/{index}",
                content_hash=str(index),
                cluster_key=str(index),
                published_at=now,
                created_at=now,
                importance=3,
            )
        )
    db.commit()
    assert len(current_stories(db)) == 10
    assert len(current_stories(db, limit=4)) == 4
