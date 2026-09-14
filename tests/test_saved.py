from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import current_saved_stories, current_stories, purge_expired_stories
from app.services.saved import normalize_article_url, parse_expiry, save_article


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_parse_expiry_default_days(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.saved.utcnow", lambda: now)
    expires = parse_expiry("7", "")
    assert expires == now + timedelta(days=7)


def test_parse_expiry_custom_date(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.saved.utcnow", lambda: now)
    expires = parse_expiry("0", "2026-09-20")
    assert expires.day == 20
    assert expires.month == 9


def test_purge_removes_expired_saved(monkeypatch):
    from app.services import briefing

    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(briefing, "utcnow", lambda: now)
    monkeypatch.setattr(briefing.env, "story_retention_days", 7)
    db = _session()
    gone = Story(
        title="Gone",
        summary="Full",
        source_name="example.com",
        canonical_url="https://example.com/gone",
        content_hash="g",
        cluster_key="g",
        saved=True,
        expires_at=now - timedelta(days=1),
        created_at=now - timedelta(days=2),
    )
    keep = Story(
        title="Keep",
        summary="Full",
        source_name="example.com",
        canonical_url="https://example.com/keep",
        content_hash="k",
        cluster_key="k",
        saved=True,
        expires_at=now + timedelta(days=3),
        created_at=now,
    )
    db.add_all([gone, keep])
    db.commit()
    removed = purge_expired_stories(db)
    db.commit()
    titles = [story.title for story in db.query(Story).all()]
    assert removed == 1
    assert titles == ["Keep"]


def test_current_saved_and_briefing_order(monkeypatch):
    from app.services import briefing

    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(briefing, "utcnow", lambda: now)
    monkeypatch.setattr(briefing.env, "story_retention_days", 7)
    db = _session()
    db.add(
        Story(
            title="Saved piece",
            summary="Full article",
            source_name="example.com",
            canonical_url="https://example.com/saved",
            content_hash="s",
            cluster_key="s",
            saved=True,
            expires_at=now + timedelta(days=7),
            created_at=now,
            published_at=now,
        )
    )
    db.add(
        Story(
            title="Feed piece",
            summary="Summary",
            source_name="BBC World",
            canonical_url="https://example.com/feed",
            content_hash="f",
            cluster_key="f",
            created_at=now,
            published_at=now,
            importance=3,
        )
    )
    db.commit()
    assert [story.title for story in current_saved_stories(db)] == ["Saved piece"]
    assert [story.title for story in current_stories(db)] == ["Saved piece", "Feed piece"]


def test_save_article_stores_full_text(monkeypatch):
    html = "<html><head><title>Hello</title></head><body><p>Enough words to count as a full article for later reading on the e-ink device.</p></body></html>"
    monkeypatch.setattr("app.services.saved._http_get_html", lambda url: html)
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract",
        lambda *_args, **_kwargs: (
            "Enough words to count as a full article for later reading on the e-ink device, "
            "including the body of the piece as scraped from the page."
        ),
    )
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract_metadata",
        lambda *_args, **_kwargs: type("M", (), {"title": "Hello there"})(),
    )
    monkeypatch.setattr("app.services.favicon.ensure_favicon", lambda *_args, **_kwargs: None)
    db = _session()
    story = save_article(db, "https://example.com/long-read", "7", "")
    assert story.saved is True
    assert story.saved_origin == "manual"
    assert story.title == "Hello there"
    assert "e-ink" in story.summary
    assert story.expires_at is not None


def test_save_article_origin_briefing(monkeypatch):
    html = "<html><head><title>Hello</title></head><body><p>Enough words to count as a full article for later reading on the e-ink device.</p></body></html>"
    monkeypatch.setattr("app.services.saved._http_get_html", lambda url: html)
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract",
        lambda *_args, **_kwargs: (
            "Enough words to count as a full article for later reading on the e-ink device, "
            "including the body of the piece as scraped from the page."
        ),
    )
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract_metadata",
        lambda *_args, **_kwargs: type("M", (), {"title": "Hello there"})(),
    )
    monkeypatch.setattr("app.services.favicon.ensure_favicon", lambda *_args, **_kwargs: None)
    db = _session()
    story = save_article(db, "https://example.com/from-briefing", "7", "", origin="briefing")
    assert story.saved_origin == "briefing"
    from app.services.saved import saved_origin_label

    assert saved_origin_label(story.saved_origin) == "From Briefing"
    assert saved_origin_label("manual") == "Added URL"


def test_normalize_article_url_adds_https():
    assert normalize_article_url("www.example.com/story") == "https://www.example.com/story"


def test_normalize_article_url_rejects_empty():
    try:
        normalize_article_url("not a url")
    except ValueError as exc:
        assert "full article URL" in str(exc)
    else:
        raise AssertionError("expected ValueError")
