from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.models import Feed
from app.services import settings


def _db_file_size() -> int:
    total = 0
    for name in ("newscast.db", "newscast.db-wal", "newscast.db-shm"):
        path = DATA_DIR / name
        if path.is_file():
            total += path.stat().st_size
    return total


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            shown = f"{size:.1f}".rstrip("0").rstrip(".")
            return f"{shown} {unit}"
        size /= 1024
    return f"{int(value)} B"


def failing_feeds(db: Session) -> list[Feed]:
    return (
        db.query(Feed)
        .filter(Feed.enabled.is_(True))
        .filter(Feed.last_error.isnot(None))
        .filter(Feed.last_error != "")
        .order_by(Feed.name.asc())
        .all()
    )


def failing_feed_count(db: Session) -> int:
    return len(failing_feeds(db))


def status_health(db: Session) -> dict:
    usage = shutil.disk_usage(DATA_DIR if DATA_DIR.exists() else Path.cwd())
    db_bytes = _db_file_size()
    failed = failing_feeds(db)
    failing = len(failed)
    ai_error = settings.get_value(db, "last_ai_error").strip()
    ai_at = settings.get_value(db, "last_ai_error_at").strip()
    if ai_error:
        ai_label = ai_error
        if ai_at:
            ai_label = f"{ai_error} ({ai_at.replace('T', ' ').replace('+00:00', ' UTC')})"
    else:
        ai_label = "OK — no recent AI errors"
    return {
        "disk_free": usage.free,
        "disk_total": usage.total,
        "disk_free_label": _format_bytes(usage.free),
        "disk_total_label": _format_bytes(usage.total),
        "disk_label": f"{_format_bytes(usage.free)} free of {_format_bytes(usage.total)}",
        "db_bytes": db_bytes,
        "db_label": _format_bytes(db_bytes),
        "failing_feeds": failing,
        "failing_items": [
            {"id": feed.id, "name": feed.name, "error": (feed.last_error or "").strip()}
            for feed in failed
        ],
        "failing_label": (
            "None"
            if failing == 0
            else f"{failing} enabled feed{'s' if failing != 1 else ''} with errors"
        ),
        "ai_error": ai_error,
        "ai_error_at": ai_at,
        "ai_label": ai_label,
    }
