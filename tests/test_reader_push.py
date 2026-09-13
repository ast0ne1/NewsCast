from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, SyncTask
from app.services import reader_push
from app.services.briefing import enqueue_sync_file


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_unreachable_leaves_pending(tmp_path: Path, monkeypatch):
    db = _session()
    path = tmp_path / "news.epub"
    path.write_bytes(b"epub")
    enqueue_sync_file(db, path, "news.epub", kind="crosspoint", save_path="/News/news.epub")
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host: False)
    result = reader_push.flush_pending(db)
    assert result["ok"] is False
    assert result["online"] is False
    assert db.query(SyncTask).one().status == "pending"


def test_upload_marks_complete(tmp_path: Path, monkeypatch):
    db = _session()
    path = tmp_path / "news.epub"
    path.write_bytes(b"epub")
    enqueue_sync_file(db, path, "news.epub", kind="crosspoint", save_path="/News/news.epub")
    uploaded: list[str] = []
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host: True)
    monkeypatch.setattr(reader_push, "upload_file", lambda host, file_path, dest: uploaded.append(file_path.name))
    result = reader_push.flush_pending(db)
    assert result["ok"] is True
    assert result["uploaded"] == 1
    assert uploaded == ["news.epub"]
    assert db.query(SyncTask).one().status == "complete"
