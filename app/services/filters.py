from __future__ import annotations

from app.models import Feed, Story


def parse_words(value: str | None) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for part in (value or "").split(","):
        word = part.strip().lower()
        if not word or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def combine_words(*values: str | None) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for value in values:
        for word in parse_words(value):
            if word in seen:
                continue
            seen.add(word)
            words.append(word)
    return words


def text_blob(*parts: str | None) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).lower()


def matches_keywords(text: str, include: list[str], exclude: list[str]) -> bool:
    haystack = (text or "").lower()
    if any(word in haystack for word in exclude):
        return False
    if include and not any(word in haystack for word in include):
        return False
    return True


def story_passes_filters(
    title: str,
    summary: str = "",
    excerpt: str = "",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> bool:
    return matches_keywords(text_blob(title, summary, excerpt), include or [], exclude or [])


def feed_keyword_lists(feed: Feed | None, global_include: str = "", global_exclude: str = "") -> tuple[list[str], list[str]]:
    include = combine_words(global_include, getattr(feed, "keyword_include", "") if feed else "")
    exclude = combine_words(global_exclude, getattr(feed, "keyword_exclude", "") if feed else "")
    return include, exclude


def story_kept(story: Story, feeds: dict[str, Feed], global_include: str = "", global_exclude: str = "") -> bool:
    if story.favourited or story.saved:
        return True
    feed = feeds.get(story.source_name)
    include, exclude = feed_keyword_lists(feed, global_include, global_exclude)
    return story_passes_filters(story.title, story.summary, story.raw_excerpt, include, exclude)
