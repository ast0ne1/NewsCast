from datetime import datetime, timezone

from app.services.briefing import briefing_title, format_published, render_txt, stories_payload


class FakeStory:
    def __init__(self):
        self.id = 7
        self.title = "Test headline"
        self.summary = "A short summary."
        self.source_name = "BBC World"
        self.canonical_url = "https://example.com/story"
        self.published_at = datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc)
        self.created_at = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)


def test_stories_payload_shape():
    payload = stories_payload([FakeStory()])
    assert payload["device"] == "xteink-x3"
    assert "generated_at" in payload
    story = payload["stories"][0]
    assert story["id"] == "7"
    assert story["title"] == "Test headline"
    assert story["source"] == "BBC World"
    assert story["published_label"] == "13 Sep 2026"


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
