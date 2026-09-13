from datetime import datetime, timedelta, timezone

from app.services.schedule import feed_interval_minutes, feed_is_due
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
