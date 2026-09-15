from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, SyncTask, utcnow
from app.services import settings
from app.services.delivery import (
    briefing_day_for_task,
    delivery_status,
    format_age,
    mark_briefing_pushed,
)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_format_age():
    assert format_age(timedelta(seconds=20)) == "less than a minute"
    assert format_age(timedelta(minutes=12)) == "12m"
    assert format_age(timedelta(hours=2, minutes=15)) == "2h 15m"
    assert format_age(timedelta(days=3)) == "3d"


def test_briefing_day_for_task_from_file_path():
    task = SyncTask(
        task_id="a",
        kind="crosspoint",
        status="pending",
        file_path="/data/briefings/news-2026-09-14.epub",
        save_path="/News/NewsCast - Work 2026-09-14.epub",
    )
    assert briefing_day_for_task(task) == date(2026, 9, 14)


def test_delivery_scheduled_when_unpublished(tmp_path: Path, monkeypatch):
    db = _session()
    settings.set_value(db, "briefing_publish_at", "06:30")
    monkeypatch.setattr(
        "app.services.delivery.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    monkeypatch.setattr(
        "app.services.briefing.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    status = delivery_status(db, now=datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc), pending=[])
    assert status["trust_key"] == "scheduled"
    assert "06:30" in status["trust"]
    assert "publishes at 06:30" in status["hint"]


def test_delivery_waiting_when_published_and_pending(tmp_path: Path, monkeypatch):
    db = _session()
    settings.set_value(db, "reader_push_when_online", "1")
    today = date(2026, 9, 14)
    path = tmp_path / f"news-{today.isoformat()}.epub"
    path.write_bytes(b"epub")
    monkeypatch.setattr(
        "app.services.delivery.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    monkeypatch.setattr(
        "app.services.briefing.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    pending = [
        SyncTask(
            task_id="b",
            kind="crosspoint",
            status="pending",
            file_path=str(path),
            save_path=f"/News/news-{today.isoformat()}.epub",
            created_at=utcnow() - timedelta(hours=2, minutes=10),
        )
    ]
    status = delivery_status(db, now=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), pending=pending)
    assert status["trust_key"] == "waiting"
    assert status["pending"] == 1
    assert status["queue_age"] == "2h 10m"
    assert "waiting" in status["trust"]


def test_delivery_push_off(tmp_path: Path, monkeypatch):
    db = _session()
    settings.set_value(db, "reader_push_when_online", "0")
    today = date(2026, 9, 14)
    path = tmp_path / f"news-{today.isoformat()}.epub"
    path.write_bytes(b"epub")
    monkeypatch.setattr(
        "app.services.delivery.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    monkeypatch.setattr(
        "app.services.briefing.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    status = delivery_status(db, now=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), pending=[])
    assert status["trust_key"] == "push_off"
    assert "push when online is off" in status["trust"]


def test_delivery_on_reader_after_mark(tmp_path: Path, monkeypatch):
    db = _session()
    today = date(2026, 9, 14)
    path = tmp_path / f"news-{today.isoformat()}.epub"
    path.write_bytes(b"epub")
    monkeypatch.setattr(
        "app.services.delivery.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    monkeypatch.setattr(
        "app.services.briefing.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    mark_briefing_pushed(db, today)
    status = delivery_status(db, now=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), pending=[])
    assert status["trust_key"] == "delivered"
    assert status["trust"] == "Morning paper is on the reader"


def test_delivery_on_reader_from_completed_task(tmp_path: Path, monkeypatch):
    db = _session()
    today = date(2026, 9, 14)
    path = tmp_path / f"news-{today.isoformat()}.epub"
    path.write_bytes(b"epub")
    monkeypatch.setattr(
        "app.services.delivery.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    monkeypatch.setattr(
        "app.services.briefing.dated_briefing_path",
        lambda day, suffix="epub", **_kwargs: tmp_path / f"news-{day}.{suffix}",
    )
    db.add(
        SyncTask(
            task_id="c",
            kind="crosspoint",
            status="complete",
            file_path=str(path),
            save_path=f"/News/news-{today.isoformat()}.epub",
            completed_at=datetime(2026, 9, 14, 7, 2, tzinfo=timezone.utc),
        )
    )
    db.commit()
    status = delivery_status(db, now=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), pending=[])
    assert status["trust_key"] == "delivered"
    assert status["last_push_label"]
