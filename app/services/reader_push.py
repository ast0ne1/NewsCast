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


def reader_host(db: Session, user_id: int | None = None) -> str:
    if user_id is not None:
        from app.services import reader_config

        return reader_config.reader_host(db, user_id)
    host = (settings.get_value(db, "reader_host") or "").strip()
    host = host.removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        return host
    return "" if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_HOST


def reader_upload_dir(db: Session, user_id: int | None = None) -> str:
    if user_id is not None:
        from app.services import reader_config

        return reader_config.reader_upload_dir(db, user_id)
    raw = (settings.get_value(db, "reader_upload_path") or "").strip()
    if not raw:
        raw = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    if not raw.startswith("/"):
        raw = "/" + raw
    fallback = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    return raw.rstrip("/") or fallback


def _http_reachable(host: str, timeout: float) -> bool:
    if not host:
        return False
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


def reader_reachable(
    host: str,
    timeout: float | None = None,
    db: Session | None = None,
    user_id: int | None = None,
) -> bool:
    limit = timeout if timeout is not None else 2.0
    if not host:
        return False
    if db is not None and user_id is not None:
        from app.services import reader_config

        if reader_config.reader_is_kobo(db, user_id):
            return _tcp_reachable(host, reader_config.reader_ssh_port(db, user_id), limit)
        return _http_reachable(host, limit)
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


def _sftp_upload(db: Session, host: str, path: Path, dest_dir: str, user_id: int | None = None) -> None:
    import paramiko

    from app.services import reader_config

    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    if user_id is not None:
        port = reader_config.reader_ssh_port(db, user_id)
        user = reader_config.reader_ssh_user(db, user_id)
        password = reader_config.reader_ssh_password(db, user_id)
    else:
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


def upload_file(host: str, path: Path, dest_dir: str, db: Session | None = None, user_id: int | None = None) -> None:
    if db is not None and user_id is not None:
        from app.services import reader_config

        if reader_config.reader_is_kobo(db, user_id):
            _sftp_upload(db, host, path, dest_dir, user_id=user_id)
            return
        _http_upload(host, path, dest_dir)
        return
    if db is not None and settings.reader_is_kobo(db):
        _sftp_upload(db, host, path, dest_dir)
        return
    _http_upload(host, path, dest_dir)


def pending_crosspoint(db: Session, user_id: int | None = None) -> list[SyncTask]:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "pending")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    return query.order_by(SyncTask.created_at.asc()).all()


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


def _queue_display_sort_key(task: SyncTask) -> tuple:
    """Today's paper first, then older papers (newest date first), then Send files (newest queued first)."""
    from app.services.delivery import briefing_day_for_task

    today = datetime.now().astimezone().date()
    day = briefing_day_for_task(task)
    created = task.created_at or datetime.min.replace(tzinfo=timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    # Negate timestamps so newer sorts first within a group.
    created_rank = -created.timestamp()
    if day == today:
        return (0, created_rank)
    if day is not None:
        return (1, -day.toordinal(), created_rank)
    return (2, created_rank)


def _created_label(value: datetime | None) -> str:
    if value is None:
        return ""
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%d %b %Y %H:%M") + " UTC"


def queue_items(db: Session, user_id: int | None = None) -> list[dict]:
    items = []
    for task in sorted(pending_crosspoint(db, user_id=user_id), key=_queue_display_sort_key):
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


def cancel_pending(db: Session, task_id: str, user_id: int | None = None) -> bool:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.task_id == task_id)
        .filter(SyncTask.status == "pending")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    task = query.first()
    if task is None:
        return False
    task.status = "cancelled"
    task.completed_at = utcnow()
    db.commit()
    return True


def enqueue_frozen_briefing(db: Session, user_id: int | None = None) -> SyncTask | None:
    uid = int(user_id or 1)
    path = frozen_briefing_path("today", suffix="epub", fallback=False, user_id=uid)
    if path is None:
        return None
    dest = reader_upload_dir(db, user_id=uid)
    day = day_from_briefing_path(path.stem) or datetime.now().date()
    save_name = paper_download_name(db, day, suffix="epub")
    return enqueue_sync_file(
        db,
        path,
        save_name,
        kind="crosspoint",
        save_path=join(dest, save_name),
        user_id=uid,
    )


def enqueue_briefing_and_library(db: Session, user_id: int | None = None) -> list[SyncTask]:
    uid = int(user_id or 1)
    dest = reader_upload_dir(db, user_id=uid)
    tasks: list[SyncTask] = []
    briefing_task = enqueue_frozen_briefing(db, user_id=uid)
    if briefing_task:
        tasks.append(briefing_task)
    for item in (
        db.query(LibraryFile)
        .filter(LibraryFile.user_id == uid)
        .order_by(LibraryFile.created_at.desc())
        .all()
    ):
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
                user_id=uid,
            )
        )
    return tasks


def _task_folder(task: SyncTask, default: str) -> str:
    folder = dirname(task.save_path or "")
    return folder if folder and folder != "." else default


def flush_pending(db: Session, user_id: int | None = None) -> dict:
    from app.services.delivery import briefing_day_for_task, mark_briefing_pushed

    uid = int(user_id) if user_id is not None else None
    host = reader_host(db, user_id=uid)
    dest = reader_upload_dir(db, user_id=uid) if uid is not None else reader_upload_dir(db)
    tasks = pending_crosspoint(db, user_id=uid)
    online = reader_reachable(host, db=db, user_id=uid)
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
        task_uid = getattr(task, "user_id", None) or uid
        try:
            upload_file(host, path, _task_folder(task, dest), db=db, user_id=task_uid)
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
    pending = len(pending_crosspoint(db, user_id=uid))
    return {"ok": True, "online": True, "uploaded": uploaded, "pending": pending, "host": host}


def flush_all_enabled(db: Session) -> list[dict]:
    """Background tick: flush each active user who has push-when-online and a host."""
    from app.models import User
    from app.services import reader_config

    results: list[dict] = []
    users = db.query(User).filter(User.active.is_(True)).order_by(User.id.asc()).all()
    for user in users:
        if not reader_config.reader_push_enabled(db, user.id):
            continue
        if not reader_config.reader_host(db, user.id):
            continue
        results.append(flush_pending(db, user_id=user.id))
    return results


def remember_probe(host: str, online: bool) -> None:
    global _last_probe
    _last_probe = {"host": host, "online": bool(online)}


def last_probe(host: str) -> dict | None:
    if _last_probe and _last_probe.get("host") == host:
        return _last_probe
    return None


def snapshot(db: Session, *, probe: bool = True, user_id: int | None = None) -> dict:
    from app.services import reader_config

    uid = int(user_id) if user_id is not None else None
    host = reader_host(db, user_id=uid)
    pending = pending_crosspoint(db, user_id=uid)
    if probe:
        online = reader_reachable(host, timeout=0.6, db=db, user_id=uid)
        remember_probe(host, online)
        checked = True
    else:
        prev = last_probe(host)
        online = None if prev is None else prev["online"]
        checked = prev is not None
    if uid is not None:
        push_on = reader_config.reader_push_enabled(db, uid)
        device = reader_config.reader_device(db, uid)
        ssh_port = reader_config.reader_ssh_port(db, uid)
        upload_path = reader_upload_dir(db, user_id=uid)
    else:
        push_on = settings.reader_push_enabled(db)
        device = settings.reader_device(db)
        ssh_port = settings.reader_ssh_port(db)
        upload_path = reader_upload_dir(db)
    return {
        "host": host,
        "upload_path": upload_path,
        "online": online,
        "checked": checked,
        "pending": len(pending),
        "queue": queue_items(db, user_id=uid),
        "push_when_online": push_on,
        "device": device,
        "ssh_port": ssh_port,
    }
