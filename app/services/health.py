from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Feed, utcnow

EMPTY_DAYS = 7
HEALTH_LABELS = {
    "ok": "Healthy",
    "empty": "Empty (7 days)",
    "error": "Error",
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_fetch(feed: Feed, *, status_code: int | None, item_count: int, now: datetime | None = None) -> None:
    when = now or utcnow()
    if status_code is not None:
        feed.last_status_code = status_code
    feed.last_item_count = item_count
    if item_count > 0:
        feed.empty_since = None
        return
    if feed.empty_since is None:
        feed.empty_since = when


def feed_health(feed: Feed, now: datetime | None = None) -> str:
    when = now or utcnow()
    if getattr(feed, "last_status_code", None) == 404:
        return "error"
    empty_since = _aware(getattr(feed, "empty_since", None))
    if empty_since and when - empty_since >= timedelta(days=EMPTY_DAYS):
        return "empty"
    if feed.last_error:
        return "error"
    return "ok"


def feed_health_label(feed: Feed, now: datetime | None = None) -> str:
    return HEALTH_LABELS[feed_health(feed, now)]
