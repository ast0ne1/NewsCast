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


def normalize_optional_clock(value: str | None) -> str:
    raw = (value or "").strip().replace(".", ":")
    if not raw:
        return ""
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return ""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def _minutes_since_midnight(value: str) -> int | None:
    clock = normalize_optional_clock(value)
    if not clock:
        return None
    hour, minute = (int(part) for part in clock.split(":"))
    return hour * 60 + minute


def within_active_hours(
    start: str | None,
    end: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True when local time is inside [start, end). Blank either side means all day."""
    start_m = _minutes_since_midnight(start or "")
    end_m = _minutes_since_midnight(end or "")
    if start_m is None or end_m is None:
        return True
    when = now or datetime.now()
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    now_m = when.hour * 60 + when.minute
    if start_m == end_m:
        return True
    if start_m < end_m:
        return start_m <= now_m < end_m
    return now_m >= start_m or now_m < end_m


def feed_uses_global_schedule(feed: Feed) -> bool:
    return getattr(feed, "schedule_mode", "global") != "custom"


def feed_is_due(
    feed: Feed,
    global_minutes: int,
    now: datetime | None = None,
    *,
    active_start: str | None = None,
    active_end: str | None = None,
    local_now: datetime | None = None,
) -> bool:
    if feed_uses_global_schedule(feed) and not within_active_hours(
        active_start,
        active_end,
        now=local_now,
    ):
        return False
    now = now or datetime.now(timezone.utc)
    last = _aware(feed.last_fetched_at)
    if last is None:
        return True
    interval = timedelta(minutes=feed_interval_minutes(feed, global_minutes))
    return now - last >= interval


def feed_is_muted(feed: Feed, now: datetime | None = None) -> bool:
    until = _aware(getattr(feed, "muted_until", None))
    if until is None:
        return False
    when = now or datetime.now(timezone.utc)
    return when < until
