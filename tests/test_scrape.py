from datetime import datetime, timezone

from app.services.scrape import (
    article_candidates,
    discover_rss,
    guess_feed_urls,
    looks_like_feed_url,
    published_from_url,
)

HOME = """
<html>
  <head>
    <link rel="alternate" type="application/rss+xml" href="/feed/" />
  </head>
  <body>
    <a href="/2026/09/13/example-story/">A long enough headline for scraping</a>
    <a href="/about">About us</a>
    <a href="https://other.example/story">Ignore offsite</a>
  </body>
</html>
"""


def test_discover_rss_from_link_tag():
    assert discover_rss("https://hackaday.com/", HOME) == "https://hackaday.com/feed/"


def test_article_candidates_skip_nav_and_offsite():
    found = article_candidates("https://hackaday.com/", HOME)
    assert found == [
        {"title": "A long enough headline for scraping", "url": "https://hackaday.com/2026/09/13/example-story/"}
    ]


def test_guess_feed_urls_from_homepage():
    assert guess_feed_urls("https://techcrunch.com/") == [
        "https://techcrunch.com/feed/",
        "https://techcrunch.com/rss.xml",
        "https://techcrunch.com/rss",
    ]


def test_looks_like_feed_url():
    assert looks_like_feed_url("https://techcrunch.com/feed/")
    assert looks_like_feed_url("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert not looks_like_feed_url("https://techcrunch.com/")


def test_published_from_url():
    when = published_from_url("https://techcrunch.com/2026/09/12/example-story/")
    assert when == datetime(2026, 9, 12, tzinfo=timezone.utc)
    assert published_from_url("https://techcrunch.com/") is None
