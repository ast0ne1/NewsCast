from datetime import datetime, timedelta, timezone

from app.models import Feed


def feed_interval_minutes(feed: Feed, global_minutes: int) -> int:
    if getattr(feed, "schedule_mode", "global") == "custom":
        custom = getattr(feed, "interval_minutes", None)
        if custom and custom > 0:
            return custom
    return max(1, global_minutes)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def feed_is_due(feed: Feed, global_minutes: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = _aware(feed.last_fetched_at)
    if last is None:
        return True
    interval = timedelta(minutes=feed_interval_minutes(feed, global_minutes))
    return now - last >= interval
