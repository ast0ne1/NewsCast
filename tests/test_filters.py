from app.models import Feed, Story
from app.services.filters import combine_words, story_kept, story_passes_filters


def test_exclude_wins_over_include():
    assert story_passes_filters("Crypto election night", include=["election"], exclude=["crypto"]) is False
    assert story_passes_filters("Election night", include=["election"], exclude=["crypto"]) is True


def test_include_requires_one_match():
    assert story_passes_filters("Market update", include=["climate", "election"], exclude=[]) is False
    assert story_passes_filters("Climate talks", include=["climate", "election"], exclude=[]) is True


def test_global_and_feed_words_combine():
    assert combine_words("climate", "election, climate") == ["climate", "election"]


def test_favourites_and_saved_always_pass():
    feed = Feed(name="TechCrunch", keyword_exclude="crypto")
    dropped = Story(title="New crypto coin", summary="", source_name="TechCrunch", raw_excerpt="", favourited=False, saved=False)
    kept_fav = Story(title="New crypto coin", summary="", source_name="TechCrunch", raw_excerpt="", favourited=True, saved=False)
    kept_saved = Story(title="New crypto coin", summary="", source_name="TechCrunch", raw_excerpt="", favourited=False, saved=True)
    feeds = {"TechCrunch": feed}
    assert story_kept(dropped, feeds, "", "") is False
    assert story_kept(kept_fav, feeds, "", "") is True
    assert story_kept(kept_saved, feeds, "", "") is True
