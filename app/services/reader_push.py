from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from posixpath import dirname, join

import httpx
from sqlalchemy.orm import Session

from app.config import LIBRARY_DIR
from app.models import LibraryFile, SyncTask, utcnow
from app.services import settings
from app.services.briefing import BRIEFING_SAVE_RE, enqueue_sync_file, frozen_briefing_path
from app.services.library import pretty_size

logger = logging.getLogger("newscast.reader_push")
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_last_probe: dict | None = None


def reader_host(db: Session) -> str:
    host = (settings.get_value(db, "reader_host") or "crosspoint.local").strip()
    return host.removeprefix("http://").removeprefix("https://").split("/")[0] or "crosspoint.local"


def reader_upload_dir(db: Session) -> str:
    raw = (settings.get_value(db, "reader_upload_path") or "/News").strip() or "/News"
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/") or "/News"


def reader_reachable(host: str, timeout: float | None = None) -> bool:
    limit = timeout if timeout is not None else 2.0
    try:
        with httpx.Client(timeout=httpx.Timeout(limit, connect=limit), follow_redirects=True) as client:
            response = client.get(f"http://{host}/api/status")
            return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def upload_file(host: str, path: Path, dest_dir: str) -> None:
    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    with path.open("rb") as handle:
        with httpx.Client(timeout=UPLOAD_TIMEOUT, follow_redirects=True) as client:
            response = client.post(
                f"http://{host}/upload",
                params={"path": folder},
                files={"file": (path.name, handle, "application/octet-stream")},
            )
            response.raise_for_status()


def pending_crosspoint(db: Session) -> list[SyncTask]:
    return (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "pending")
        .order_by(SyncTask.created_at.asc())
        .all()
    )


def queue_label(task: SyncTask) -> str:
    name = Path(task.save_path or task.file_path).name
    match = BRIEFING_SAVE_RE.search(name)
    if match:
        day = date.fromisoformat(match.group(1))
        if day == datetime.now().date():
            return "Today's briefing"
        return f"Briefing · {day.strftime('%d %b %Y')}"
    return f"Send: {name}"


def _created_label(value: datetime | None) -> str:
    if value is None:
        return ""
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%d %b %Y %H:%M") + " UTC"


def queue_items(db: Session) -> list[dict]:
    items = []
    for task in pending_crosspoint(db):
        name = Path(task.save_path or task.file_path).name
        items.append(
            {
                "task_id": task.task_id,
                "label": queue_label(task),
                "name": name,
                "size_label": pretty_size(task.size or 0),
                "created_label": _created_label(task.created_at),
            }
        )
    return items


def cancel_pending(db: Session, task_id: str) -> bool:
    task = (
        db.query(SyncTask)
        .filter(SyncTask.task_id == task_id)
        .filter(SyncTask.status == "pending")
        .first()
    )
    if task is None:
        return False
    task.status = "cancelled"
    task.completed_at = utcnow()
    db.commit()
    return True


def enqueue_frozen_briefing(db: Session) -> SyncTask | None:
    path = frozen_briefing_path("today", suffix="epub", fallback=False)
    if path is None:
        return None
    dest = reader_upload_dir(db)
    date_part = path.stem.removeprefix("news-")
    save_name = f"NewsCast-{date_part}.epub"
    return enqueue_sync_file(db, path, save_name, kind="crosspoint", save_path=join(dest, save_name))


def enqueue_briefing_and_library(db: Session) -> list[SyncTask]:
    dest = reader_upload_dir(db)
    tasks: list[SyncTask] = []
    briefing_task = enqueue_frozen_briefing(db)
    if briefing_task:
        tasks.append(briefing_task)
    for item in db.query(LibraryFile).order_by(LibraryFile.created_at.desc()).all():
        path = LIBRARY_DIR / item.stored_name
        if not path.exists():
            continue
        name = item.original_name or path.name
        tasks.append(enqueue_sync_file(db, path, name, kind="crosspoint", save_path=join(dest, name)))
    return tasks


def _task_folder(task: SyncTask, default: str) -> str:
    folder = dirname(task.save_path or "")
    return folder if folder and folder != "." else default


def flush_pending(db: Session) -> dict:
    host = reader_host(db)
    dest = reader_upload_dir(db)
    tasks = pending_crosspoint(db)
    online = reader_reachable(host)
    if not online:
        return {"ok": False, "online": False, "uploaded": 0, "pending": len(tasks), "host": host}
    uploaded = 0
    for task in tasks:
        path = Path(task.file_path)
        if not path.exists():
            task.status = "failed"
            task.completed_at = utcnow()
            continue
        try:
            upload_file(host, path, _task_folder(task, dest))
            task.status = "complete"
            task.completed_at = utcnow()
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("upload failed for %s: %s", path.name, exc)
    db.commit()
    pending = len(pending_crosspoint(db))
    return {"ok": True, "online": True, "uploaded": uploaded, "pending": pending, "host": host}


def remember_probe(host: str, online: bool) -> None:
    global _last_probe
    _last_probe = {"host": host, "online": bool(online)}


def last_probe(host: str) -> dict | None:
    if _last_probe and _last_probe.get("host") == host:
        return _last_probe
    return None


def snapshot(db: Session, *, probe: bool = True) -> dict:
    host = reader_host(db)
    pending = pending_crosspoint(db)
    if probe:
        online = reader_reachable(host, timeout=0.6)
        remember_probe(host, online)
        checked = True
    else:
        prev = last_probe(host)
        online = None if prev is None else prev["online"]
        checked = prev is not None
    return {
        "host": host,
        "upload_path": reader_upload_dir(db),
        "online": online,
        "checked": checked,
        "pending": len(pending),
        "queue": queue_items(db),
        "push_when_online": settings.reader_push_enabled(db),
    }
