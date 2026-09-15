from __future__ import annotations

import logging
import socket
from datetime import date, datetime, timezone
from pathlib import Path
from posixpath import dirname, join

import httpx
from sqlalchemy.orm import Session

from app.config import DATA_DIR, LIBRARY_DIR
from app.models import LibraryFile, SyncTask, utcnow
from app.services import settings
from app.services.briefing import BRIEFING_SAVE_RE, enqueue_sync_file, frozen_briefing_path
from app.services.library import pretty_size
from app.services.paper_naming import day_from_briefing_path, paper_download_name

logger = logging.getLogger("newscast.reader_push")
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_last_probe: dict | None = None


def reader_host(db: Session) -> str:
    host = (settings.get_value(db, "reader_host") or "").strip()
    host = host.removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        return host
    return "" if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_HOST


def reader_upload_dir(db: Session) -> str:
    raw = (settings.get_value(db, "reader_upload_path") or "").strip()
    if not raw:
        raw = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    if not raw.startswith("/"):
        raw = "/" + raw
    fallback = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    return raw.rstrip("/") or fallback


def _http_reachable(host: str, timeout: float) -> bool:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=timeout), follow_redirects=True) as client:
            response = client.get(f"http://{host}/api/status")
            return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reader_reachable(host: str, timeout: float | None = None, db: Session | None = None) -> bool:
    limit = timeout if timeout is not None else 2.0
    if db is not None and settings.reader_is_kobo(db):
        return _tcp_reachable(host, settings.reader_ssh_port(db), limit)
    return _http_reachable(host, limit)


def _http_upload(host: str, path: Path, dest_dir: str) -> None:
    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    with path.open("rb") as handle:
        with httpx.Client(timeout=UPLOAD_TIMEOUT, follow_redirects=True) as client:
            response = client.post(
                f"http://{host}/upload",
                params={"path": folder},
                files={"file": (path.name, handle, "application/octet-stream")},
            )
            response.raise_for_status()


def _known_hosts_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "reader_known_hosts"


def _ensure_sftp_dir(sftp, folder: str) -> None:
    parts = [part for part in folder.split("/") if part]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def _sftp_upload(db: Session, host: str, path: Path, dest_dir: str) -> None:
    import paramiko

    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    port = settings.reader_ssh_port(db)
    user = settings.reader_ssh_user(db)
    password = settings.get_value(db, "reader_ssh_password")
    client = paramiko.SSHClient()
    keys = _known_hosts_path()
    client.load_system_host_keys()
    if keys.exists():
        client.load_host_keys(str(keys))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password or None,
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
            auth_timeout=20,
        )
        client.save_host_keys(str(keys))
        sftp = client.open_sftp()
        try:
            _ensure_sftp_dir(sftp, folder)
            sftp.put(str(path), f"{folder.rstrip('/')}/{path.name}")
        finally:
            sftp.close()
    finally:
        client.close()


def upload_file(host: str, path: Path, dest_dir: str, db: Session | None = None) -> None:
    if db is not None and settings.reader_is_kobo(db):
        _sftp_upload(db, host, path, dest_dir)
        return
    _http_upload(host, path, dest_dir)


def pending_crosspoint(db: Session) -> list[SyncTask]:
    return (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "pending")
        .order_by(SyncTask.created_at.asc())
        .all()
    )


def queue_label(task: SyncTask) -> str:
    from app.services.delivery import briefing_day_for_task

    day = briefing_day_for_task(task)
    today = datetime.now().astimezone().date()
    if day == today:
        return "Today's paper"
    if day is not None:
        return f"Paper · {day.strftime('%d %b %Y')}"
    name = Path(task.save_path or task.file_path).name
    stem = Path(name).stem or name
    return f"File · {stem}"


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
    day = day_from_briefing_path(path.stem) or datetime.now().date()
    save_name = paper_download_name(db, day, suffix="epub")
    return enqueue_sync_file(db, path, save_name, kind="crosspoint", save_path=join(dest, save_name))


def enqueue_briefing_and_library(db: Session) -> list[SyncTask]:
    dest = reader_upload_dir(db)
    tasks: list[SyncTask] = []
    briefing_task = enqueue_frozen_briefing(db)
    if briefing_task:
        tasks.append(briefing_task)
    for item in db.query(LibraryFile).order_by(LibraryFile.created_at.desc()).all():
        from app.services.library import library_path

        path = library_path(item)
        if not path.exists():
            continue
        name = item.original_name or path.name
        tasks.append(
            enqueue_sync_file(
                db,
                path,
                name,
                kind="crosspoint",
                save_path=join(dest, name),
                user_id=item.user_id,
            )
        )
    return tasks


def _task_folder(task: SyncTask, default: str) -> str:
    folder = dirname(task.save_path or "")
    return folder if folder and folder != "." else default


def flush_pending(db: Session) -> dict:
    from app.services.delivery import briefing_day_for_task, mark_briefing_pushed

    host = reader_host(db)
    dest = reader_upload_dir(db)
    tasks = pending_crosspoint(db)
    online = reader_reachable(host, db=db)
    if not online:
        return {"ok": False, "online": False, "uploaded": 0, "pending": len(tasks), "host": host}
    uploaded = 0
    today = datetime.now().astimezone().date()
    for task in tasks:
        path = Path(task.file_path)
        if not path.exists():
            task.status = "failed"
            task.completed_at = utcnow()
            continue
        try:
            upload_file(host, path, _task_folder(task, dest), db=db)
            task.status = "complete"
            task.completed_at = utcnow()
            uploaded += 1
            if briefing_day_for_task(task) == today:
                mark_briefing_pushed(db, today)
                from app.services import ntfy
                from app.services.paper_naming import paper_display_title

                label = Path(task.save_path or path.name).name or paper_display_title(db, today)
                instance = (settings.get_value(db, "instance_name") or "").strip() or "NewsCast"
                ntfy.notify(
                    db,
                    kind="push",
                    title=instance,
                    body=f"Morning paper is on the reader — {label}",
                    user_id=getattr(task, "user_id", None),
                )
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
        online = reader_reachable(host, timeout=0.6, db=db)
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
        "device": settings.reader_device(db),
        "ssh_port": settings.reader_ssh_port(db),
    }
