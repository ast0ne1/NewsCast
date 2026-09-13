import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.services.briefing import briefing_title, format_published, render_txt, sanitize_html, stories_payload, write_epub


class FakeStory:
    def __init__(self, saved=False, source="BBC World"):
        self.id = 7
        self.title = "Test headline"
        self.summary = "A short summary."
        self.source_name = source
        self.canonical_url = "https://example.com/story"
        self.published_at = datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc)
        self.created_at = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
        self.saved = saved


class FakeFeed:
    def __init__(self, category="news"):
        self.category = category


def test_stories_payload_shape():
    payload = stories_payload([FakeStory()])
    assert payload["device"] == "xteink-x3"
    assert "generated_at" in payload
    story = payload["stories"][0]
    assert story["id"] == "7"
    assert story["title"] == "Test headline"
    assert story["source"] == "BBC World"
    assert story["published_label"] == "13 Sep 2026"
    assert story["category"] == "news"
    assert story["saved"] is False


def test_txt_briefing_includes_story():
    payload = stories_payload([FakeStory()])
    text = render_txt(payload)
    assert "NewsCast briefing" in text
    assert "Test headline" in text
    assert "A short summary." in text
    assert "13 Sep 2026" in text


def test_format_published_empty():
    assert format_published(None) == ""


def test_instance_name_in_briefing():
    payload = stories_payload([FakeStory()], instance_name="Work")
    assert payload["title"] == "NewsCast · Work"
    assert render_txt(payload).startswith("NewsCast · Work")
    assert briefing_title("") == "NewsCast briefing"


def test_saved_story_uses_long_reads_category():
    payload = stories_payload([FakeStory(saved=True)])
    assert payload["stories"][0]["category"] == "longreads"
    assert payload["stories"][0]["category_label"] == "Long reads"
    assert "Long reads" in render_txt(payload)


def test_payload_uses_feed_category():
    payload = stories_payload(
        [FakeStory()],
        feeds={"BBC World": FakeFeed("technology")},
        labels={"technology": "Tech"},
    )
    assert payload["stories"][0]["category"] == "technology"
    assert payload["stories"][0]["category_label"] == "Tech"


def test_sanitize_html_strips_script_and_images():
    cleaned = sanitize_html('<p>Hello</p><script>alert(1)</script><img src="http://x/y.jpg"><p onclick="x">Body</p>')
    assert "Hello" in cleaned
    assert "Body" in cleaned
    assert "<script" not in cleaned
    assert "alert(1)" not in cleaned
    assert "<img" not in cleaned
    assert "onclick" not in cleaned


def test_write_epub_strips_unsafe_html_and_groups_toc(tmp_path: Path):
    dest = tmp_path / "news.epub"
    payload = {
        "title": "NewsCast briefing",
        "generated_at": "2026-09-14T06:30:00Z",
        "stories": [
            {
                "id": "1",
                "title": "Saved essay",
                "summary": "<p>Long read.</p>",
                "source": "Saved",
                "url": "https://example.com/saved",
                "published_label": "14 Sep 2026",
                "category": "longreads",
                "category_label": "Long reads",
                "saved": True,
            },
            {
                "id": "2",
                "title": "Wire story",
                "summary": '<p>Hello</p><script>alert(1)</script><img src="http://x/y.jpg">',
                "source": "BBC",
                "url": "https://example.com/a",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
                "saved": False,
            },
        ],
    }
    write_epub(payload, dest)
    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        chapters = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.endswith(".xhtml")
        )
        assert "<script" not in chapters
        assert "alert(1)" not in chapters
        assert "<img" not in chapters
        assert "Hello" in chapters
        toc_name = next(name for name in names if name.endswith(("nav.xhtml", "toc.ncx")))
        toc = archive.read(toc_name).decode("utf-8", errors="ignore")
        assert "Long reads" in toc
        assert "World News" in toc
        css = next(name for name in names if name.endswith("eink.css"))
        assert "Georgia" in archive.read(css).decode("utf-8")
