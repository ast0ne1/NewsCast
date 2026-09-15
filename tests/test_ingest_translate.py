from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services.ingest import run_ingest


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ingest_translates_before_store(monkeypatch):
    db = _session()
    db.add(
        Feed(
            name="DR News",
            url="https://www.dr.dk/nyheder/service/feeds/allenyheder",
            enabled=True,
            type="rss",
            category="nordic",
            translate=True,
            summarize=False,
        )
    )
    db.commit()

    def fake_collect(_feed):
        return [
            {
                "title": "Statsministeren gik af",
                "url": "https://www.dr.dk/nyheder/politik/eksempel",
                "excerpt": "Regeringen træder tilbage i aften.",
                "published_at": datetime(2026, 9, 13, tzinfo=timezone.utc),
                "source": "DR News",
            }
        ]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    monkeypatch.setattr(
        "app.services.translate.translate_story",
        lambda title, excerpt, **_kwargs: ("The prime minister resigned", "The government is stepping down tonight."),
    )
    monkeypatch.setattr("app.services.briefing.enqueue_latest_briefing", lambda _db: None)
    monkeypatch.setattr("app.services.briefing.purge_expired_stories", lambda _db: None)

    result = run_ingest(db, force=True)
    assert result["ok"] is True
    story = db.query(Story).one()
    assert story.title == "The prime minister resigned"
    assert "stepping down" in story.summary
    assert "Statsministeren" not in story.title
