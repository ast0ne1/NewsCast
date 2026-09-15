from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services import settings
from app.services.briefing import (
    category_slot_targets,
    current_stories,
    seats_from_percents,
)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_seats_from_percents_uses_largest_remainder():
    result = seats_from_percents(10, {"a": 25, "b": 50, "c": 25})
    assert result["b"] == 5
    assert result["a"] + result["c"] == 5
    assert sum(result.values()) == 10
    assert sum(seats_from_percents(20, {"a": 33, "b": 33, "c": 34}).values()) == 20


def test_category_slot_targets_explicit_and_auto_remainder():
    targets = category_slot_targets(
        20,
        {"news": 25, "technology": 50},
        ["news", "technology", "nordic", "australia"],
    )
    assert targets["news"] == 5
    assert targets["technology"] == 10
    assert targets["nordic"] + targets["australia"] == 5
    assert sum(targets.values()) == 20


def test_category_slot_targets_zero_excludes():
    targets = category_slot_targets(10, {"news": 100, "technology": 0}, ["news", "technology", "nordic"])
    assert targets["news"] == 10
    assert targets["technology"] == 0
    assert targets["nordic"] == 0


def test_parse_and_encode_category_shares():
    assert settings.parse_category_shares('{"news":25,"technology":50}') == {
        "news": 25,
        "technology": 50,
    }
    assert settings.parse_category_shares("nope") == {}
    encoded = settings.encode_category_shares({"technology": 50, "news": 25})
    assert encoded == '{"news":25,"technology":50}'


def test_current_stories_respects_category_mix(monkeypatch):
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    settings.set_value(db, "briefing_limit", "20")
    settings.set_value(db, "briefing_category_mix", "1")
    settings.set_value(db, "briefing_category_shares", '{"news":25,"technology":50}')
    db.add_all(
        [
            Feed(name="World Desk", url="https://example.com/world", category="news", enabled=True),
            Feed(name="Tech Desk", url="https://example.com/tech", category="technology", enabled=True),
            Feed(name="Nordic Desk", url="https://example.com/nordic", category="nordic", enabled=True),
            Feed(name="Oz Desk", url="https://example.com/oz", category="australia", enabled=True),
        ]
    )
    for index in range(12):
        db.add(
            Story(
                title=f"World {index}",
                summary="Summary",
                source_name="World Desk",
                canonical_url=f"https://example.com/world/{index}",
                content_hash=f"world-{index}",
                cluster_key=f"world-{index}",
                published_at=now,
                created_at=now,
                importance=3,
            )
        )
    for index in range(12):
        db.add(
            Story(
                title=f"Tech {index}",
                summary="Summary",
                source_name="Tech Desk",
                canonical_url=f"https://example.com/tech/{index}",
                content_hash=f"tech-{index}",
                cluster_key=f"tech-{index}",
                published_at=now,
                created_at=now,
                importance=3,
            )
        )
    for index in range(8):
        db.add(
            Story(
                title=f"Nordic {index}",
                summary="Summary",
                source_name="Nordic Desk",
                canonical_url=f"https://example.com/nordic/{index}",
                content_hash=f"nordic-{index}",
                cluster_key=f"nordic-{index}",
                published_at=now,
                created_at=now,
                importance=3,
            )
        )
    for index in range(8):
        db.add(
            Story(
                title=f"Oz {index}",
                summary="Summary",
                source_name="Oz Desk",
                canonical_url=f"https://example.com/oz/{index}",
                content_hash=f"oz-{index}",
                cluster_key=f"oz-{index}",
                published_at=now,
                created_at=now,
                importance=3,
            )
        )
    db.commit()

    stories = current_stories(db)
    assert len(stories) == 20
    by_source = {}
    for story in stories:
        by_source[story.source_name] = by_source.get(story.source_name, 0) + 1
    assert by_source["World Desk"] == 5
    assert by_source["Tech Desk"] == 10
    assert by_source["Nordic Desk"] + by_source["Oz Desk"] == 5
