from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services import settings
from app.services.briefing import current_stories, digest_blurb, masthead_line, stories_payload
from app.services.importance import clamp_importance, heuristic_importance, score_importance


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_heuristic_boosts_major_headlines():
    soft = heuristic_importance("Five tips for better sleep", "A short lifestyle piece.", category="culture")
    major = heuristic_importance("Breaking: earthquake hits capital", "Rescuers search rubble after a powerful quake.", category="news")
    assert soft <= 3
    assert major >= 4


def test_clamp_importance():
    assert clamp_importance(0) == 1
    assert clamp_importance(9) == 5
    assert clamp_importance(None) == 3


def test_score_without_llm_uses_heuristic():
    score = score_importance("Apple announces new iPhone", "The company unveiled the device today.", source="Verge", category="technology")
    assert 1 <= score <= 5


def test_current_stories_filters_by_min_importance(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    settings.set_value(db, "briefing_min_importance", "4")
    settings.set_value(db, "briefing_limit", "20")
    db.add(Feed(name="BBC World", url="https://example.com/rss", category="news", enabled=True))
    db.add(
        Story(
            title="Ignore me",
            summary="Minor note.",
            source_name="BBC World",
            canonical_url="https://example.com/1",
            content_hash="1",
            cluster_key="1",
            published_at=now,
            created_at=now,
            importance=2,
        )
    )
    db.add(
        Story(
            title="Keep me",
            summary="Major update.",
            source_name="BBC World",
            canonical_url="https://example.com/2",
            content_hash="2",
            cluster_key="2",
            published_at=now,
            created_at=now,
            importance=5,
        )
    )
    db.add(
        Story(
            title="Favourite low score",
            summary="Still kept.",
            source_name="BBC World",
            canonical_url="https://example.com/3",
            content_hash="3",
            cluster_key="3",
            published_at=now,
            created_at=now,
            importance=1,
            favourited=True,
        )
    )
    db.commit()
    titles = {story.title for story in current_stories(db)}
    assert titles == {"Keep me", "Favourite low score"}


def test_digest_and_masthead():
    assert digest_blurb("First sentence. Second sentence is longer.") == "First sentence."
    payload = stories_payload(
        [
            type(
                "S",
                (),
                {
                    "id": 1,
                    "title": "One",
                    "summary": "Alpha summary.",
                    "source_name": "BBC",
                    "canonical_url": "https://example.com/a",
                    "published_at": datetime(2026, 9, 13, tzinfo=timezone.utc),
                    "created_at": datetime(2026, 9, 13, tzinfo=timezone.utc),
                    "saved": False,
                },
            )()
        ],
        feeds={"BBC": type("F", (), {"category": "technology"})()},
        labels={"technology": "Tech"},
    )
    line = masthead_line(payload["stories"])
    assert line.startswith("1 story")
    assert "tech" in line.lower()
