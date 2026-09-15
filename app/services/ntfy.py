from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.services import settings

logger = logging.getLogger("newscast.ntfy")
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
DEFAULT_SERVER = "https://ntfy.sh"


def normalize_server(value: str | None) -> str:
    raw = (value or "").strip().rstrip("/")
    return raw or DEFAULT_SERVER


def ntfy_enabled(db: Session) -> bool:
    return settings.flag_enabled(db, "ntfy_enabled")


def ntfy_server(db: Session) -> str:
    return normalize_server(settings.get_value(db, "ntfy_server"))


def ntfy_topic(db: Session) -> str:
    return (settings.get_value(db, "ntfy_topic") or "").strip()


def ntfy_token(db: Session) -> str:
    return (settings.get_value(db, "ntfy_token") or "").strip()


def notify_on_publish(db: Session) -> bool:
    return settings.flag_enabled(db, "ntfy_notify_on_publish")


def notify_on_push(db: Session) -> bool:
    return settings.flag_enabled(db, "ntfy_notify_on_push")


def _today() -> date:
    return datetime.now().astimezone().date()


def _already_notified(db: Session, kind: str, day: date) -> bool:
    key = "ntfy_last_publish_notified_day" if kind == "publish" else "ntfy_last_push_notified_day"
    return settings.get_value(db, key).strip() == day.isoformat()


def _mark_notified(db: Session, kind: str, day: date) -> None:
    key = "ntfy_last_publish_notified_day" if kind == "publish" else "ntfy_last_push_notified_day"
    settings.set_value(db, key, day.isoformat())


def notify(db: Session, *, kind: str, title: str, body: str) -> bool:
    """Best-effort ntfy publish. Never raises into callers."""
    event = (kind or "").strip().lower()
    if event not in {"publish", "push"}:
        return False
    if not ntfy_enabled(db):
        return False
    if event == "publish" and not notify_on_publish(db):
        return False
    if event == "push" and not notify_on_push(db):
        return False
    topic = ntfy_topic(db)
    if not topic:
        return False
    day = _today()
    if _already_notified(db, event, day):
        return False

    server = ntfy_server(db)
    url = f"{server}/{quote(topic, safe='')}"
    headers = {
        "Title": (title or "NewsCast").strip()[:120] or "NewsCast",
        "Tags": "newspaper",
    }
    token = ntfy_token(db)
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
    _mark_notified(db, event, day)
    return True
