from datetime import date, datetime, timezone
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
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host, timeout=None, db=None, user_id=None: False)
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
        lambda host, timeout=None, db=None, user_id=None: called.append(host) or True,
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


def test_queue_label_distinguishes_briefing_and_send(monkeypatch):
    today = date(2026, 9, 15)
    monkeypatch.setattr(
        "app.services.reader_push.datetime",
        type("DT", (), {"now": staticmethod(lambda: datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))}),
    )
    briefing = SyncTask(
        save_path="/News/My Morning Paper - 15-09-2026.epub",
        file_path=f"/data/briefings/news-{today.isoformat()}.epub",
    )
    older = SyncTask(
        save_path="/News/NewsCast-2026-09-14.epub",
        file_path="/data/briefings/news-2026-09-14.epub",
    )
    send = SyncTask(save_path="/News/notes.epub", file_path="/library/notes.epub")
    assert reader_push.queue_label(briefing) == "Today's paper"
    assert reader_push.queue_label(older) == "Paper · 14 Sep 2026"
    assert reader_push.queue_label(send) == "File · notes"


def test_queue_items_lists_today_then_older_papers_then_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.services.reader_push.datetime",
        type("DT", (), {"now": staticmethod(lambda: datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))}),
    )
    db = _session()
    older = tmp_path / "news-2026-09-14.epub"
    mid = tmp_path / "news-2026-09-15.epub"
    today = tmp_path / "news-2026-09-16.epub"
    send = tmp_path / "notes.epub"
    for path in (older, mid, today, send):
        path.write_bytes(b"epub")
    # Enqueue oldest first so FIFO would list Send/older before today.
    enqueue_sync_file(db, send, "notes.epub", kind="crosspoint", save_path="/News/notes.epub")
    enqueue_sync_file(db, older, "old.epub", kind="crosspoint", save_path="/News/old.epub")
    enqueue_sync_file(db, mid, "mid.epub", kind="crosspoint", save_path="/News/mid.epub")
    enqueue_sync_file(db, today, "today.epub", kind="crosspoint", save_path="/News/today.epub")
    labels = [item["label"] for item in reader_push.queue_items(db)]
    assert labels == [
        "Today's paper",
        "Paper · 15 Sep 2026",
        "Paper · 14 Sep 2026",
        "File · notes",
    ]


def test_queue_is_scoped_per_user(tmp_path: Path):
    db = _session()
    admin_file = tmp_path / "admin.epub"
    member_file = tmp_path / "member.epub"
    admin_file.write_bytes(b"admin")
    member_file.write_bytes(b"member")
    enqueue_sync_file(
        db,
        admin_file,
        "admin.epub",
        kind="crosspoint",
        save_path="/News/admin.epub",
        user_id=1,
    )
    enqueue_sync_file(
        db,
        member_file,
        "member.epub",
        kind="crosspoint",
        save_path="/News/member.epub",
        user_id=2,
    )
    assert [item["name"] for item in reader_push.queue_items(db, user_id=1)] == ["admin.epub"]
    assert [item["name"] for item in reader_push.queue_items(db, user_id=2)] == ["member.epub"]
    assert reader_push.cancel_pending(db, db.query(SyncTask).filter(SyncTask.user_id == 1).one().task_id, user_id=2) is False
    assert reader_push.cancel_pending(db, db.query(SyncTask).filter(SyncTask.user_id == 1).one().task_id, user_id=1) is True
    assert reader_push.pending_crosspoint(db, user_id=1) == []
    assert len(reader_push.pending_crosspoint(db, user_id=2)) == 1


def test_enqueue_briefing_and_library_only_own_files(tmp_path: Path, monkeypatch):
    from app.models import LibraryFile
    from app.services import library

    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(reader_push, "frozen_briefing_path", lambda *a, **k: None)
    db = _session()
    admin_dir = tmp_path / "1"
    member_dir = tmp_path / "2"
    admin_dir.mkdir()
    member_dir.mkdir()
    (admin_dir / "admin.pdf").write_bytes(b"a")
    (member_dir / "member.pdf").write_bytes(b"m")
    db.add(
        LibraryFile(
            user_id=1,
            stored_name="1/admin.pdf",
            original_name="admin.pdf",
            title="Admin",
            size=1,
        )
    )
    db.add(
        LibraryFile(
            user_id=2,
            stored_name="2/member.pdf",
            original_name="member.pdf",
            title="Member",
            size=1,
        )
    )
    db.commit()
    tasks = reader_push.enqueue_briefing_and_library(db, user_id=2)
    assert len(tasks) == 1
    assert tasks[0].user_id == 2
    assert Path(tasks[0].file_path).name == "member.pdf"


def test_upload_marks_complete(tmp_path: Path, monkeypatch):
    db = _session()
    path = tmp_path / "news.epub"
    path.write_bytes(b"epub")
    enqueue_sync_file(db, path, "news.epub", kind="crosspoint", save_path="/News/news.epub")
    uploaded: list[str] = []
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host, timeout=None, db=None, user_id=None: True)
    monkeypatch.setattr(
        reader_push,
        "upload_file",
        lambda host, file_path, dest, db=None, user_id=None: uploaded.append(file_path.name),
    )
    result = reader_push.flush_pending(db)
    assert result["ok"] is True
    assert result["uploaded"] == 1
    assert uploaded == ["news.epub"]
    assert db.query(SyncTask).one().status == "complete"


def test_reader_device_defaults_to_xteink():
    db = _session()
    from app.services import settings

    assert settings.reader_device(db) == "xteink"
    assert settings.normalize_reader_device("Kobo") == "kobo"
    assert settings.normalize_reader_device("nope") == "xteink"
    assert reader_push.reader_host(db) == "crosspoint.local"
    assert reader_push.reader_upload_dir(db) == "/News"


def test_kobo_defaults_host_and_folder():
    db = _session()
    from app.services import settings

    settings.set_value(db, "reader_device", "kobo")
    assert settings.reader_is_kobo(db) is True
    assert reader_push.reader_host(db) == ""
    assert reader_push.reader_upload_dir(db) == "/mnt/onboard/News"
    assert settings.reader_ssh_port(db) == 2222
    assert settings.reader_ssh_user(db) == "root"


def test_kobo_reachable_uses_tcp_port(monkeypatch):
    db = _session()
    from app.services import settings

    settings.set_value(db, "reader_device", "kobo")
    settings.set_value(db, "reader_host", "kobo.local")
    seen: list[tuple] = []

    def fake_tcp(host, port, timeout):
        seen.append((host, port, timeout))
        return True

    monkeypatch.setattr(reader_push, "_tcp_reachable", fake_tcp)
    monkeypatch.setattr(reader_push, "_http_reachable", lambda *a, **k: (_ for _ in ()).throw(AssertionError("http")))
    assert reader_push.reader_reachable("kobo.local", timeout=0.5, db=db) is True
    assert seen == [("kobo.local", 2222, 0.5)]


def test_xteink_upload_uses_http(tmp_path: Path, monkeypatch):
    db = _session()
    path = tmp_path / "paper.epub"
    path.write_bytes(b"epub")
    seen: list[tuple] = []

    def fake_http(host, file_path, dest_dir):
        seen.append((host, file_path.name, dest_dir))

    monkeypatch.setattr(reader_push, "_http_upload", fake_http)
    monkeypatch.setattr(reader_push, "_sftp_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sftp")))
    reader_push.upload_file("crosspoint.local", path, "/News", db=db)
    assert seen == [("crosspoint.local", "paper.epub", "/News")]


def test_kobo_upload_uses_sftp(tmp_path: Path, monkeypatch):
    db = _session()
    from app.services import settings

    settings.set_value(db, "reader_device", "kobo")
    path = tmp_path / "paper.epub"
    path.write_bytes(b"epub")
    seen: list[tuple] = []

    def fake_sftp(_db, host, file_path, dest_dir):
        seen.append((host, file_path.name, dest_dir))

    monkeypatch.setattr(reader_push, "_sftp_upload", fake_sftp)
    monkeypatch.setattr(reader_push, "_http_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("http")))
    reader_push.upload_file("192.168.1.8", path, "/mnt/onboard/News", db=db)
    assert seen == [("192.168.1.8", "paper.epub", "/mnt/onboard/News")]
