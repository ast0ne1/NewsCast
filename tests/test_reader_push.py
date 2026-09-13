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


def test_snapshot_skips_probe_until_remembered(monkeypatch):
    db = _session()
    called: list[str] = []
    monkeypatch.setattr(
        reader_push,
        "reader_reachable",
        lambda host, timeout=None: called.append(host) or True,
    )
    reader_push._last_probe = None
    snap = reader_push.snapshot(db, probe=False)
    assert snap["checked"] is False
    assert snap["online"] is None
    assert called == []

    snap = reader_push.snapshot(db, probe=True)
    assert snap["checked"] is True
    assert snap["online"] is True
    assert called
    called.clear()

    snap = reader_push.snapshot(db, probe=False)
    assert snap["checked"] is True
    assert snap["online"] is True
    assert called == []


def test_enqueue_dedupes_same_save_path(tmp_path: Path):
    db = _session()
    path = tmp_path / "news.epub"
    path.write_bytes(b"a")
    first = enqueue_sync_file(db, path, "news.epub", kind="crosspoint", save_path="/News/news.epub")
    path.write_bytes(b"abc")
    second = enqueue_sync_file(db, path, "news.epub", kind="crosspoint", save_path="/News/news.epub")
    assert first.id == second.id
    assert db.query(SyncTask).count() == 1
    assert second.size == 3


def test_cancel_pending_removes_from_queue(tmp_path: Path):
    db = _session()
    path = tmp_path / "notes.epub"
    path.write_bytes(b"epub")
    task = enqueue_sync_file(db, path, "notes.epub", kind="crosspoint", save_path="/News/notes.epub")
    assert reader_push.cancel_pending(db, task.task_id) is True
    assert reader_push.pending_crosspoint(db) == []
    assert db.query(SyncTask).one().status == "cancelled"
    assert path.exists()


def test_queue_label_distinguishes_briefing_and_send():
    briefing = SyncTask(save_path="/News/NewsCast-2026-09-14.epub", file_path="x")
    send = SyncTask(save_path="/News/notes.epub", file_path="x")
    assert "briefing" in reader_push.queue_label(briefing).lower()
    assert reader_push.queue_label(send) == "Send: notes.epub"


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
