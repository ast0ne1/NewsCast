from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import current_stories, purge_expired_stories


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _story(db: Session, **kwargs) -> Story:
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    story = Story(
        title=kwargs.get("title", "Headline"),
        summary="Summary",
        source_name=kwargs.get("source_name", "BBC World"),
        canonical_url=kwargs.get("url", "https://example.com/" + kwargs.get("title", "a")),
        content_hash=kwargs.get("title", "a"),
        cluster_key=kwargs.get("title", "a"),
        published_at=kwargs.get("published_at", now),
        created_at=kwargs.get("created_at", now),
        favourited=kwargs.get("favourited", False),
        importance=kwargs.get("importance", 3),
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


def test_purge_removes_old_unfavourited(monkeypatch):
    from app.services import briefing

    monkeypatch.setattr(briefing, "utcnow", lambda: datetime(2026, 9, 13, tzinfo=timezone.utc))
    monkeypatch.setattr(briefing.env, "story_retention_days", 7)
    db = _session()
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    fresh = datetime(2026, 9, 12, tzinfo=timezone.utc)
    _story(db, title="Old", url="https://example.com/old", published_at=old, created_at=old)
    kept_fav = _story(
        db,
        title="Old fav",
        url="https://example.com/fav",
        published_at=old,
        created_at=old,
        favourited=True,
    )
    kept_new = _story(db, title="New", url="https://example.com/new", published_at=fresh, created_at=fresh)
    removed = purge_expired_stories(db)
    db.commit()
    ids = {story.id for story in db.query(Story).all()}
    assert removed == 1
    assert kept_fav.id in ids
    assert kept_new.id in ids
    assert len(ids) == 2


def test_current_stories_keeps_old_favourites(monkeypatch):
    from app.services import briefing

    monkeypatch.setattr(briefing, "utcnow", lambda: datetime(2026, 9, 13, tzinfo=timezone.utc))
    monkeypatch.setattr(briefing.env, "story_retention_days", 7)
    db = _session()
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    _story(db, title="Gone", url="https://example.com/gone", published_at=old, created_at=old)
    fav = _story(
        db,
        title="Kept",
        url="https://example.com/kept",
        published_at=old,
        created_at=old,
        favourited=True,
    )
    titles = [story.title for story in current_stories(db)]
    assert titles == ["Kept"]
    assert fav.title == "Kept"


def test_current_stories_keeps_a_slot_per_source(monkeypatch):
    from app.services import briefing

    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(briefing, "utcnow", lambda: now)
    monkeypatch.setattr(briefing.env, "story_retention_days", 7)
    db = _session()
    for index in range(8):
        _story(
            db,
            title=f"BBC {index}",
            url=f"https://example.com/bbc-{index}",
            published_at=now,
            created_at=now,
        )
    older = datetime(2026, 9, 12, tzinfo=timezone.utc)
    _story(
        db,
        title="TechCrunch piece",
        url="https://techcrunch.com/2026/09/12/hello/",
        published_at=older,
        created_at=older,
        source_name="TechCrunch",
    )
    titles = [story.title for story in current_stories(db, limit=4)]
    assert "TechCrunch piece" in titles
