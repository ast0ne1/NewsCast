from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.services import settings, user_settings

logger = logging.getLogger("newscast.ntfy")
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
DEFAULT_SERVER = "https://ntfy.sh"

USER_NTFY_KEYS = (
    "ntfy_enabled",
    "ntfy_topic",
    "ntfy_token",
    "ntfy_notify_on_publish",
    "ntfy_notify_on_push",
    "ntfy_last_publish_notified_day",
    "ntfy_last_push_notified_day",
)


def normalize_server(value: str | None) -> str:
    raw = (value or "").strip().rstrip("/")
    return raw or DEFAULT_SERVER


def migrate_user_ntfy_from_instance(db: Session, user_id: int) -> None:
    """Copy instance ntfy keys into user_settings once if the user has none yet."""
    has_any = any(user_settings.get_value(db, user_id, key).strip() for key in USER_NTFY_KEYS)
    if has_any:
        return
    for key in USER_NTFY_KEYS:
        inst = settings.get_value(db, key)
        if inst.strip():
            user_settings.set_value(db, user_id, key, inst)


def _user_raw(db: Session, user_id: int | None, key: str) -> str:
    if user_id is None:
        return settings.get_value(db, key)
    value = user_settings.get_value(db, user_id, key)
    if value.strip():
        return value
    return settings.get_value(db, key)


def _user_flag(db: Session, user_id: int | None, key: str) -> bool:
    raw = _user_raw(db, user_id, key).strip().lower()
    return raw in {"1", "true", "on", "yes"}


def ntfy_enabled(db: Session, user_id: int | None = None) -> bool:
    return _user_flag(db, user_id, "ntfy_enabled")


def ntfy_server(db: Session, user_id: int | None = None) -> str:
    """Blank user server falls back to household (instance) ntfy_server."""
    if user_id is not None:
        user_srv = user_settings.get_value(db, user_id, "ntfy_server").strip()
        if user_srv:
            return normalize_server(user_srv)
    return normalize_server(settings.get_value(db, "ntfy_server"))


def ntfy_topic(db: Session, user_id: int | None = None) -> str:
    return _user_raw(db, user_id, "ntfy_topic").strip()


def ntfy_token(db: Session, user_id: int | None = None) -> str:
    return _user_raw(db, user_id, "ntfy_token").strip()


def notify_on_publish(db: Session, user_id: int | None = None) -> bool:
    return _user_flag(db, user_id, "ntfy_notify_on_publish")


def notify_on_push(db: Session, user_id: int | None = None) -> bool:
    return _user_flag(db, user_id, "ntfy_notify_on_push")


def _today() -> date:
    return datetime.now().astimezone().date()


def _day_key(kind: str) -> str:
    return "ntfy_last_publish_notified_day" if kind == "publish" else "ntfy_last_push_notified_day"


def _already_notified(db: Session, kind: str, day: date, user_id: int | None) -> bool:
    key = _day_key(kind)
    if user_id is not None:
        return user_settings.get_value(db, user_id, key).strip() == day.isoformat()
    return settings.get_value(db, key).strip() == day.isoformat()


def _mark_notified(db: Session, kind: str, day: date, user_id: int | None) -> None:
    key = _day_key(kind)
    if user_id is not None:
        user_settings.set_value(db, user_id, key, day.isoformat())
    else:
        settings.set_value(db, key, day.isoformat())


def notify(db: Session, *, kind: str, title: str, body: str, user_id: int | None = None) -> bool:
    """Best-effort ntfy publish. Never raises into callers."""
    event = (kind or "").strip().lower()
    if event not in {"publish", "push"}:
        return False
    if user_id is not None:
        from app.models import User
        from app.services import users as users_service

        user = db.get(User, user_id)
        # Missing user row → allow instance-level config (legacy / single-user publish).
        if user is not None and not users_service.user_may_use_ntfy(user):
            return False
        migrate_user_ntfy_from_instance(db, user_id)
    if not ntfy_enabled(db, user_id):
        return False
    if event == "publish" and not notify_on_publish(db, user_id):
        return False
    if event == "push" and not notify_on_push(db, user_id):
        return False
    topic = ntfy_topic(db, user_id)
    if not topic:
        return False
    day = _today()
    if _already_notified(db, event, day, user_id):
        return False

    server = ntfy_server(db, user_id)
    url = f"{server}/{quote(topic, safe='')}"
    headers = {
        "Title": (title or "NewsCast").strip()[:120] or "NewsCast",
        "Tags": "newspaper",
    }
    token = ntfy_token(db, user_id)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    message = (body or "").strip() or headers["Title"]
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.post(url, content=message.encode("utf-8"), headers=headers)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ntfy %s notify failed: %s", event, exc)
        return False
    _mark_notified(db, event, day, user_id)
    return True
