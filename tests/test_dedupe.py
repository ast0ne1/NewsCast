from app.services.dedupe import canonicalize_url, content_hash, is_duplicate_title, title_similarity


def test_canonicalize_strips_tracking():
    url = "https://www.Example.com/story?utm_source=rss&id=1#top"
    assert canonicalize_url(url) == "https://example.com/story?id=1"


def test_duplicate_titles_cluster():
    left = "Markets rally after surprise rate cut"
    right = "Markets rally after a surprise rate cut"
    assert is_duplicate_title(left, right)
    assert title_similarity(left, "Unrelated sports result") < 0.4


def test_content_hash_stable():
    assert content_hash("Hello", "World") == content_hash("hello", "World")
