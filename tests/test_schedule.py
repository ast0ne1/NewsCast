from datetime import datetime, timedelta, timezone

from app.services.schedule import (
    feed_interval_minutes,
    feed_is_due,
    normalize_optional_clock,
    within_active_hours,
)
from app.services.settings import format_interval_short


class FakeFeed:
    def __init__(self, schedule_mode="global", interval_minutes=None, last_fetched_at=None):
        self.schedule_mode = schedule_mode
        self.interval_minutes = interval_minutes
        self.last_fetched_at = last_fetched_at


def test_format_interval_uses_hours_over_one_hour():
    assert format_interval_short(15) == "15 min"
    assert format_interval_short(60) == "1 hr"
    assert format_interval_short(120) == "2 hrs"
    assert format_interval_short(1440) == "24 hrs"


def test_custom_interval_wins():
    feed = FakeFeed(schedule_mode="custom", interval_minutes=15)
    assert feed_interval_minutes(feed, 60) == 15


def test_global_interval_used_by_default():
    feed = FakeFeed()
    assert feed_interval_minutes(feed, 60) == 60


def test_feed_due_when_never_fetched():
    assert feed_is_due(FakeFeed(), 60)


def test_feed_not_due_inside_window():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    feed = FakeFeed(last_fetched_at=now - timedelta(minutes=10))
    assert not feed_is_due(feed, 60, now=now)


def test_custom_feed_due_after_its_interval():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    feed = FakeFeed(
        schedule_mode="custom",
        interval_minutes=15,
        last_fetched_at=now - timedelta(minutes=16),
    )
    assert feed_is_due(feed, 60, now=now)


def test_normalize_optional_clock():
    assert normalize_optional_clock("") == ""
    assert normalize_optional_clock("9:00") == "09:00"
    assert normalize_optional_clock("12:30") == "12:30"
    assert normalize_optional_clock("25:00") == ""


def test_within_active_hours_blank_is_all_day():
    noon = datetime(2026, 9, 14, 12, 0)
    assert within_active_hours("", "", now=noon)
    assert within_active_hours("09:00", "", now=noon)
    assert within_active_hours("", "12:00", now=noon)


def test_within_active_hours_same_day_window():
    assert within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 9, 0))
    assert within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 11, 59))
    assert not within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 12, 0))
    assert not within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 8, 59))


def test_within_active_hours_overnight():
    assert within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 23, 0))
    assert within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 5, 0))
    assert not within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 12, 0))


def test_global_feed_respects_active_hours():
    utc_now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    feed = FakeFeed(last_fetched_at=utc_now - timedelta(hours=2))
    assert feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 10, 0),
    )
    assert not feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 13, 0),
    )


def test_custom_feed_ignores_active_hours():
    utc_now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    feed = FakeFeed(
        schedule_mode="custom",
        interval_minutes=15,
        last_fetched_at=utc_now - timedelta(minutes=20),
    )
    assert feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 3, 0),
    )
