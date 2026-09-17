from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import SyncTask, utcnow
from app.services import settings
from app.services.briefing import BRIEFING_SAVE_RE, briefing_publish_at, dated_briefing_path, paper_status
from app.services.paper_naming import day_from_briefing_path

ISO_DAY_IN_NAME = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _local_today(now: datetime | None = None) -> date:
    when = now or datetime.now()
    return when.astimezone().date() if when.tzinfo else when.date()


def format_age(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "less than a minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 48:
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    days = hours // 24
    return f"{days}d"


def format_local_when(value: datetime | None) -> str:
    when = _aware(value)
    if when is None:
        return ""
    return when.astimezone().strftime("%d %b %Y %H:%M")


def briefing_day_for_task(task: SyncTask) -> date | None:
    path = Path(task.file_path or "")
    day = day_from_briefing_path(path.stem)
    if day:
        return day
    name = Path(task.save_path or path.name).name
    match = BRIEFING_SAVE_RE.search(name)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass
    found = ISO_DAY_IN_NAME.search(name)
    if found:
        try:
            return date.fromisoformat(found.group(1))
        except ValueError:
            return None
    return None


def _published_at(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _latest_complete(db: Session, user_id: int | None = None) -> SyncTask | None:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "complete")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    return query.order_by(SyncTask.completed_at.desc(), SyncTask.id.desc()).first()


def _latest_briefing_complete(db: Session, day: date, user_id: int | None = None) -> SyncTask | None:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "complete")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    tasks = query.order_by(SyncTask.completed_at.desc(), SyncTask.id.desc()).limit(40).all()
    for task in tasks:
        if briefing_day_for_task(task) == day:
            return task
    return None


def _pending_briefing(db: Session, day: date, pending: list[SyncTask]) -> bool:
    return any(briefing_day_for_task(task) == day for task in pending)


def mark_briefing_pushed(db: Session, day: date) -> None:
    settings.set_value(db, "last_briefing_pushed_day", day.isoformat())


def briefing_pushed_today(db: Session, day: date, user_id: int | None = None) -> bool:
    stored = settings.get_value(db, "last_briefing_pushed_day").strip()
    if stored == day.isoformat():
        return True
    return _latest_briefing_complete(db, day, user_id=user_id) is not None


def delivery_status(
    db: Session,
    *,
    now: datetime | None = None,
    pending: list[SyncTask] | None = None,
    user_id: int | None = None,
) -> dict:
    from app.services.reader_push import pending_crosspoint, queue_label

    when = now or datetime.now().astimezone()
    today = _local_today(when)
    paper = paper_status(db, now=when, user_id=user_id)
    path = dated_briefing_path(today, user_id=user_id)
    published_at = _published_at(path) if paper["published"] else None
    pending_tasks = pending if pending is not None else pending_crosspoint(db, user_id=user_id)
    oldest = pending_tasks[0] if pending_tasks else None
    oldest_created = _aware(oldest.created_at) if oldest else None
    queue_age = format_age(utcnow() - oldest_created) if oldest_created else None
    last_push = _latest_complete(db, user_id=user_id)
    last_briefing = _latest_briefing_complete(db, today, user_id=user_id)
    if user_id is not None:
        from app.services import reader_config

        push_on = reader_config.reader_push_enabled(db, user_id)
    else:
        push_on = settings.reader_push_enabled(db)
    pushed = briefing_pushed_today(db, today, user_id=user_id)
    pending_briefing = _pending_briefing(db, today, pending_tasks)

    if not paper["published"]:
        trust = f"Morning paper publishes at {paper['publish_at']}"
        trust_key = "scheduled"
    elif pushed:
        trust = "Morning paper is on the reader"
        trust_key = "delivered"
    elif not push_on and not pending_tasks:
        trust = "Morning paper published — push when online is off"
        trust_key = "push_off"
    else:
        trust = "Morning paper published — waiting to reach the reader"
        trust_key = "waiting"

    last_push_label = format_local_when(last_push.completed_at) if last_push else ""
    last_push_item = queue_label(last_push) if last_push else ""
    last_briefing_label = format_local_when(last_briefing.completed_at) if last_briefing else ""
    if pushed and not last_briefing_label:
        stored_day = settings.get_value(db, "last_briefing_pushed_day").strip()
        if stored_day == today.isoformat():
            last_briefing_label = "today (recorded)"

    hint_bits = []
    if published_at:
        hint_bits.append(f"published {format_local_when(published_at)}")
    else:
        hint_bits.append(f"publishes at {paper['publish_at']}")
    if last_push_label:
        hint_bits.append(f"last push {last_push_label}")
    else:
        hint_bits.append("no push yet")
    if pending_tasks:
        age = queue_age or "just now"
        hint_bits.append(f"{len(pending_tasks)} queued ({age} waiting)")
    else:
        hint_bits.append("nothing queued")

    return {
        "publish_at": paper["publish_at"],
        "published": paper["published"],
        "published_at": published_at,
        "published_label": format_local_when(published_at) if published_at else "",
        "last_push_at": _aware(last_push.completed_at) if last_push else None,
        "last_push_label": last_push_label,
        "last_push_item": last_push_item,
        "last_briefing_push_at": _aware(last_briefing.completed_at) if last_briefing else None,
        "last_briefing_push_label": last_briefing_label,
        "pending": len(pending_tasks),
        "queue_age": queue_age,
        "queue_age_label": queue_age or "",
        "pending_briefing": pending_briefing,
        "push_when_online": push_on,
        "pushed_today": pushed,
        "trust": trust,
        "trust_key": trust_key,
        "hint": "Today: " + " · ".join(hint_bits),
    }
