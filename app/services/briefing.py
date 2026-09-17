from __future__ import annotations

import html
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from ebooklib import epub
from sqlalchemy import delete, func, or_
from sqlalchemy.orm import Session

from app.config import BRIEFING_DIR, env
from app.models import Feed, Story, SyncTask, utcnow
from app.services import settings
from app.services.categories import BUILTIN_LABELS, DEFAULT_CATEGORY, category_labels, slugify
from app.services.cover_image import render_newspaper_cover
from app.services.filters import story_kept
from app.services.paper_naming import (
    format_paper_date,
    paper_category_display_title,
    paper_display_title,
    paper_download_name,
    reader_date_format,
)

BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
MAX_BRIEFING_STORIES = 20
BRIEFING_DAYS = {"today", "yesterday", "all"}
BRIEFING_SAVE_RE = re.compile(r"(?:NewsCast|news)-(\d{4}-\d{2}-\d{2})(?:-[a-z0-9-]+)?\.(epub|txt)$", re.I)
ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CATEGORY_PAPER_STEM_RE = re.compile(r"^news-(\d{4}-\d{2}-\d{2})-([a-z0-9-]+)$", re.I)
KEEP_DATED_BRIEFINGS = 7
DEFAULT_PUBLISH_AT = "06:30"
SAVED_CATEGORY = "longreads"
SAVED_CATEGORY_LABEL = "Long reads"


def briefing_dir_for(user_id: int | None = None) -> Path:
    uid = int(user_id or 1)
    path = BRIEFING_DIR / str(uid)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scope_stories(query, user_id: int | None):
    if user_id is not None:
        return query.filter(Story.user_id == user_id)
    return query


def _scope_feeds(query, user_id: int | None):
    if user_id is not None:
        return query.filter(Feed.user_id == user_id)
    return query
EINK_CSS = """
body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1.12em;
  line-height: 1.5;
  color: #111;
  background: #fff;
  margin: 1.1em 1.2em 1.6em;
}
h1 {
  font-size: 1.55em;
  line-height: 1.25;
  margin: 0 0 0.35em;
  font-weight: bold;
}
h2 {
  font-size: 1.2em;
  line-height: 1.3;
  margin: 1.4em 0 0.55em;
  font-weight: bold;
}
h3 {
  font-size: 1.05em;
  line-height: 1.3;
  margin: 1.25em 0 0.45em;
  font-weight: bold;
}
h4 {
  font-size: 0.98em;
  line-height: 1.3;
  margin: 0.95em 0 0.35em;
  font-weight: normal;
  font-style: italic;
}
p { margin: 0 0 0.75em; }
a { color: #111; text-decoration: underline; }
.meta { font-style: italic; color: #333; margin: 0 0 0.35em; }
.byline { font-style: italic; color: #333; margin: 0 0 1em; }
.cover-image { margin: 0 0 1em; text-align: center; }
.cover-image img { width: 100%; height: auto; }
.cover-rule {
  border: 0;
  border-top: 1px solid #111;
  margin: 0.9em 0 1.1em;
}
.contents h2 { margin-top: 0.4em; }
.contents .toc-category { margin: 0 0 1.1em; }
.contents .toc-category h3 {
  margin: 0 0 0.55em;
  padding-bottom: 0.25em;
  border-bottom: 1px solid #111;
}
.contents .toc-source { margin: 0 0 0.85em; }
.contents .toc-source h4 { margin: 0.7em 0 0.4em; }
.contents ol.toc-stories {
  list-style: none;
  padding: 0;
  margin: 0;
}
.contents ol.toc-stories li {
  margin: 0 0 0.85em;
  padding: 0;
  line-height: 1.4;
}
.contents ol.toc-stories li .toc-title {
  display: block;
  font-weight: bold;
  margin: 0 0 0.2em;
}
.contents ol.toc-stories li .toc-digest {
  display: block;
  font-size: 0.92em;
  font-weight: normal;
  font-style: normal;
  color: #333;
  line-height: 1.35;
}
.masthead {
  margin: 0 0 0.75em;
  font-style: italic;
  color: #333;
}
.story { page-break-before: always; }
.story h1 { margin-bottom: 0.45em; }
.story .body p { margin-bottom: 0.85em; }
.story .original { margin-top: 1.2em; }
img { display: none; }
"""
_ALLOWED_TAGS = {"p", "br", "em", "strong", "b", "i", "u", "a", "ul", "ol", "li", "blockquote", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style"}
_DROP_TAGS = {"img", "video", "audio", "source", "iframe", "object", "embed"}


def normalize_briefing_day(value: str | None) -> str:
    key = (value or "today").strip().lower()
    if ISO_DAY_RE.fullmatch(key):
        return key
    return key if key in BRIEFING_DAYS else "today"


def paper_day_for(value: str | None = "today", now: datetime | None = None) -> date | None:
    key = normalize_briefing_day(value)
    if ISO_DAY_RE.fullmatch(key):
        try:
            return date.fromisoformat(key)
        except ValueError:
            return None
    today = _local_today(now)
    if key == "yesterday":
        return today - timedelta(days=1)
    if key == "all":
        return None
    return today


def briefing_path(day: str | None = "today") -> str:
    key = normalize_briefing_day(day)
    return "/?day=yesterday" if key == "yesterday" else "/"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _day_window(day: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    key = normalize_briefing_day(day)
    if key == "all":
        return None
    target = paper_day_for(key, now=now)
    if target is None:
        return None
    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _in_day(story: Story, window: tuple[datetime, datetime] | None) -> bool:
    if window is None:
        return True
    when = _aware(story.published_at or story.created_at)
    if when is None:
        return False
    start, end = window
    return start <= when < end


def _apply_keyword_filters(db: Session, stories: list[Story]) -> list[Story]:
    feeds = {feed.name: feed for feed in db.query(Feed).all()}
    include = settings.get_value(db, "keyword_include")
    exclude = settings.get_value(db, "keyword_exclude")
    return [story for story in stories if story_kept(story, feeds, include, exclude)]


def _story_category(story: Story, feeds: dict[str, Feed]) -> str:
    if getattr(story, "saved", False):
        return SAVED_CATEGORY
    feed = feeds.get(story.source_name)
    return getattr(feed, "category", None) or DEFAULT_CATEGORY


def _apply_importance_filter(db: Session, stories: list[Story]) -> list[Story]:
    from app.services.importance import effective_importance

    minimum = settings.briefing_min_importance(db)
    feeds = {feed.name: feed for feed in db.query(Feed).all()}
    kept: list[Story] = []
    for story in stories:
        if getattr(story, "saved", False) or getattr(story, "favourited", False):
            kept.append(story)
            continue
        if effective_importance(story, category=_story_category(story, feeds)) >= minimum:
            kept.append(story)
    return kept


def digest_blurb(summary: str, *, limit: int = 160) -> str:
    text = summary or ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    compact = " ".join(text.split()).strip()
    if not compact:
        return ""
    for sep in (". ", "! ", "? "):
        index = compact.find(sep)
        if 8 <= index <= limit:
            return compact[: index + 1].strip()
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 1].rsplit(" ", 1)[0]
    return (clipped or compact[: limit - 1]).rstrip(".,;:") + "…"


def masthead_line(stories: list[dict]) -> str:
    if not stories:
        return "No stories yet"
    total = len(stories)
    parts = [f"{total} stor{'y' if total == 1 else 'ies'}"]
    for _key, label, group in group_stories(stories):
        parts.append(f"{len(group)} {label.lower()}")
    return " · ".join(parts)


def retention_cutoff() -> datetime:
    return utcnow() - timedelta(days=env.story_retention_days)


def _story_age():
    return func.coalesce(Story.published_at, Story.created_at)


def _saved_still_current():
    now = utcnow()
    return or_(Story.expires_at.is_(None), Story.expires_at >= now)


def current_saved_stories(db: Session, user_id: int | None = None) -> list[Story]:
    query = db.query(Story).filter(Story.saved.is_(True)).filter(_saved_still_current())
    query = _scope_stories(query, user_id)
    return query.order_by(Story.created_at.desc()).all()


def _diverse_recent(stories: list[Story], limit: int) -> list[Story]:
    by_source: dict[str, list[Story]] = {}
    for story in stories:
        by_source.setdefault(story.source_name, []).append(story)
    picked: list[Story] = []
    seen: set[int] = set()
    index = 0
    while len(picked) < limit:
        added = False
        for group in by_source.values():
            if index >= len(group):
                continue
            story = group[index]
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)
            added = True
            if len(picked) >= limit:
                return picked
        if not added:
            break
        index += 1
    return picked


def seats_from_percents(limit: int, percents: dict[str, float]) -> dict[str, int]:
    if limit <= 0 or not percents:
        return {key: 0 for key in percents}
    raw = {key: limit * (value / 100.0) for key, value in percents.items()}
    floors = {key: int(value) for key, value in raw.items()}
    remaining = limit - sum(floors.values())
    order = sorted(
        raw.keys(),
        key=lambda key: (raw[key] - floors[key], percents[key], key),
        reverse=True,
    )
    for key in order[: max(0, remaining)]:
        floors[key] += 1
    return floors


def category_slot_targets(
    limit: int,
    shares: dict[str, int],
    category_keys: list[str],
) -> dict[str, int]:
    """Map categories to story counts. Explicit % win slots; blank keys share leftover; 0 excludes."""
    keys = [key for key in category_keys if key]
    if limit <= 0 or not keys:
        return {key: 0 for key in keys}

    fixed = {key: shares[key] for key in keys if key in shares and shares[key] > 0}
    excluded = {key for key in keys if shares.get(key) == 0}
    auto = [key for key in keys if key not in fixed and key not in excluded]

    fixed_sum = sum(fixed.values())
    if fixed_sum > 100 and fixed:
        scaled = {key: (value * 100.0) / fixed_sum for key, value in fixed.items()}
        targets = seats_from_percents(limit, scaled)
        return {key: targets.get(key, 0) if key not in excluded else 0 for key in keys}

    remaining_pct = max(0, 100 - fixed_sum)
    percents: dict[str, float] = {key: float(value) for key, value in fixed.items()}
    if auto and remaining_pct > 0:
        each = remaining_pct / len(auto)
        for key in auto:
            percents[key] = each
    elif not auto and remaining_pct > 0 and fixed:
        boost = remaining_pct / len(fixed)
        for key in fixed:
            percents[key] = fixed[key] + boost

    targets = seats_from_percents(limit, percents) if percents else {}
    return {key: targets.get(key, 0) if key not in excluded else 0 for key in keys}


def _pick_by_category_mix(
    stories: list[Story],
    feeds: dict[str, Feed],
    limit: int,
    shares: dict[str, int],
) -> list[Story]:
    if limit <= 0 or not stories:
        return []

    by_category: dict[str, list[Story]] = {}
    for story in stories:
        key = _story_category(story, feeds)
        by_category.setdefault(key, []).append(story)
    for key, group in list(by_category.items()):
        by_category[key] = _diverse_recent(group, len(group))

    category_keys = sorted(set(by_category) | set(shares))
    targets = category_slot_targets(limit, shares, category_keys)

    picked: list[Story] = []
    seen: set[int] = set()
    leftovers: list[Story] = []
    for key in category_keys:
        pool = by_category.get(key, [])
        take = targets.get(key, 0)
        for story in pool[:take]:
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)
        leftovers.extend(story for story in pool[take:] if story.id not in seen)

    if len(picked) < limit:
        leftovers.sort(
            key=lambda story: story.published_at or story.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for story in leftovers:
            if len(picked) >= limit:
                break
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)

    return picked[:limit]


def current_stories(
    db: Session,
    limit: int | None = None,
    day: str | None = None,
    user_id: int | None = None,
) -> list[Story]:
    if limit is None:
        limit = settings.briefing_limit(db)
    cutoff = retention_cutoff()
    age = _story_age()
    window = _day_window(day) if day else None
    include_saved = day != "yesterday"
    saved = current_saved_stories(db, user_id=user_id) if include_saved else []
    favourites_q = (
        db.query(Story)
        .filter(Story.favourited.is_(True))
        .filter(or_(Story.saved.is_(False), Story.saved.is_(None)))
    )
    favourites = _scope_stories(favourites_q, user_id).order_by(age.desc()).all()
    pool_size = max(limit * 8, 80) if settings.briefing_category_mix_enabled(db) else max(limit * 4, 40)
    recent_q = (
        db.query(Story)
        .filter(or_(Story.favourited.is_(False), Story.favourited.is_(None)))
        .filter(or_(Story.saved.is_(False), Story.saved.is_(None)))
        .filter(age >= cutoff)
    )
    recent = _scope_stories(recent_q, user_id).order_by(age.desc()).limit(pool_size).all()
    favourites = [story for story in favourites if _in_day(story, window)]
    recent = [story for story in recent if _in_day(story, window)]

    feeds = {feed.name: feed for feed in _scope_feeds(db.query(Feed), user_id).all()}
    saved = _apply_keyword_filters(db, saved)
    favourites = _apply_keyword_filters(db, favourites)
    recent = _apply_importance_filter(db, _apply_keyword_filters(db, recent))

    if settings.briefing_category_mix_enabled(db):
        selected = _pick_by_category_mix(
            recent,
            feeds,
            limit,
            settings.briefing_category_shares(db),
        )
    else:
        selected = _diverse_recent(recent, limit)

    seen = {story.id for story in saved}
    stories = [*saved]
    for story in [*favourites, *selected]:
        if story.id not in seen:
            stories.append(story)
            seen.add(story.id)
    feed_stories = [story for story in stories if not story.saved]
    feed_stories.sort(key=lambda story: story.published_at or story.created_at or cutoff, reverse=True)
    return [*[story for story in stories if story.saved], *feed_stories]


def search_stories(db: Session, query: str, limit: int = 50, user_id: int | None = None) -> list[Story]:
    term = (query or "").strip()
    if not term:
        return []
    pattern = f"%{term}%"
    cutoff = retention_cutoff()
    age = _story_age()
    rows_q = (
        db.query(Story)
        .filter(
            or_(
                Story.title.ilike(pattern),
                Story.summary.ilike(pattern),
                Story.source_name.ilike(pattern),
                Story.raw_excerpt.ilike(pattern),
            )
        )
        .filter(
            or_(
                Story.favourited.is_(True),
                Story.saved.is_(True),
                age >= cutoff,
            )
        )
    )
    rows = _scope_stories(rows_q, user_id).order_by(age.desc()).limit(limit).all()
    kept: list[Story] = []
    now = utcnow()
    for story in rows:
        if story.saved and story.expires_at is not None:
            expires = story.expires_at if story.expires_at.tzinfo else story.expires_at.replace(tzinfo=timezone.utc)
            if expires < now and not story.favourited:
                continue
        kept.append(story)
    return kept


def purge_expired_stories(db: Session) -> int:
    cutoff = retention_cutoff()
    now = utcnow()
    regular = db.execute(
        delete(Story).where(
            or_(Story.saved.is_(False), Story.saved.is_(None)),
            or_(Story.favourited.is_(False), Story.favourited.is_(None)),
            _story_age() < cutoff,
        )
    )
    saved = db.execute(
        delete(Story).where(
            Story.saved.is_(True),
            Story.expires_at.isnot(None),
            Story.expires_at < now,
        )
    )
    return (regular.rowcount or 0) + (saved.rowcount or 0)


def format_published(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %b %Y")


def briefing_title(instance_name: str = "") -> str:
    name = (instance_name or "").strip()
    return f"NewsCast · {name}" if name else "NewsCast briefing"


def normalize_publish_at(value: str | None) -> str:
    raw = (value or "").strip().replace(".", ":")
    if not raw:
        return DEFAULT_PUBLISH_AT
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return DEFAULT_PUBLISH_AT
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEFAULT_PUBLISH_AT
    return f"{hour:02d}:{minute:02d}"


def briefing_publish_at(db: Session) -> str:
    return normalize_publish_at(settings.get_value(db, "briefing_publish_at"))


def dated_stem(day: date) -> str:
    return f"news-{day.isoformat()}"


def dated_category_stem(day: date, category: str) -> str:
    slug = slugify(category) or DEFAULT_CATEGORY
    return f"news-{day.isoformat()}-{slug}"


def dated_briefing_path(day: date, suffix: str = "epub", user_id: int | None = None) -> Path:
    return briefing_dir_for(user_id) / f"{dated_stem(day)}.{suffix}"


def dated_category_path(day: date, category: str, suffix: str = "epub", user_id: int | None = None) -> Path:
    return briefing_dir_for(user_id) / f"{dated_category_stem(day, category)}.{suffix}"


def parse_category_from_stem(stem: str) -> str | None:
    match = CATEGORY_PAPER_STEM_RE.match((stem or "").strip())
    return match.group(2).lower() if match else None


def _local_today(now: datetime | None = None) -> date:
    when = now or datetime.now()
    return when.date() if isinstance(when, datetime) else when


def frozen_briefing_path(
    day: str | None = "today",
    *,
    suffix: str = "epub",
    fallback: bool = True,
    now: datetime | None = None,
    category: str | None = None,
    user_id: int | None = None,
) -> Path | None:
    key = normalize_briefing_day(day)
    target = paper_day_for(key, now=now)
    if target is None:
        return None
    slug = slugify(category or "") if category else ""
    path = (
        dated_category_path(target, slug, suffix, user_id=user_id)
        if slug
        else dated_briefing_path(target, suffix, user_id=user_id)
    )
    if path.exists():
        return path
    if fallback and key == "today":
        yesterday = _local_today(now) - timedelta(days=1)
        prior = (
            dated_category_path(yesterday, slug, suffix, user_id=user_id)
            if slug
            else dated_briefing_path(yesterday, suffix, user_id=user_id)
        )
        if prior.exists():
            return prior
    return None


def available_daily_papers(*, now: datetime | None = None, days: int = 2, user_id: int | None = None) -> list[date]:
    today = _local_today(now)
    found: list[date] = []
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        if dated_briefing_path(day, user_id=user_id).exists():
            found.append(day)
    return found


def available_category_papers(
    category: str,
    *,
    now: datetime | None = None,
    days: int = 2,
    user_id: int | None = None,
) -> list[date]:
    slug = slugify(category)
    if not slug:
        return []
    today = _local_today(now)
    found: list[date] = []
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        if dated_category_path(day, slug, user_id=user_id).exists():
            found.append(day)
    return found


def category_keys_with_papers(*, now: datetime | None = None, days: int = 2, user_id: int | None = None) -> list[str]:
    today = _local_today(now)
    found: set[str] = set()
    root = briefing_dir_for(user_id)
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        for path in root.glob(f"news-{day.isoformat()}-*.epub"):
            key = parse_category_from_stem(path.stem)
            if key:
                found.add(key)
    return sorted(found)


def write_category_briefing_files(
    db: Session,
    payload: dict,
    day: date,
    user_id: int | None = None,
) -> list[Path]:
    enabled = settings.briefing_category_opds_keys(db)
    root = briefing_dir_for(user_id)
    for path in root.glob(f"news-{day.isoformat()}-*.epub"):
        key = parse_category_from_stem(path.stem)
        if key and key not in enabled:
            path.unlink(missing_ok=True)
            path.with_suffix(".txt").unlink(missing_ok=True)
    if not enabled:
        return []
    labels = category_labels(db)
    grouped: dict[str, list[dict]] = {}
    for story in payload.get("stories") or []:
        key = slugify(str(story.get("category") or DEFAULT_CATEGORY)) or DEFAULT_CATEGORY
        if key not in enabled:
            continue
        grouped.setdefault(key, []).append(story)

    written: list[Path] = []
    for key, stories in grouped.items():
        if not stories:
            continue
        label = labels.get(key) or stories[0].get("category_label") or key
        cat_payload = dict(payload)
        cat_payload["stories"] = stories
        title = paper_category_display_title(db, day, label)
        cat_payload["title"] = title
        cat_payload["paper_title"] = title
        cat_payload["paper_id"] = f"newscast-{day.isoformat()}-{key}"
        paths = write_briefing_files(cat_payload, stem=dated_category_stem(day, key), user_id=user_id)
        written.append(paths["epub"])
    return written


def prune_old_briefings(keep: int = KEEP_DATED_BRIEFINGS, user_id: int | None = None) -> int:
    from app.services.paper_naming import day_from_briefing_path

    root = briefing_dir_for(user_id)
    main_files = sorted(root.glob("news-????-??-??.epub"), reverse=True)
    keep_days = {day_from_briefing_path(path.stem) for path in main_files[:keep]}
    keep_days.discard(None)
    removed = 0
    for path in root.glob("news-*.epub"):
        day = day_from_briefing_path(path.stem)
        if day is None or day in keep_days:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(".txt").unlink(missing_ok=True)
        removed += 1
    for path in root.glob("news-*.txt"):
        day = day_from_briefing_path(path.stem)
        if day is None or day in keep_days:
            continue
        path.unlink(missing_ok=True)
    return removed


def paper_status(db: Session, now: datetime | None = None, user_id: int | None = None) -> dict:
    when = now or datetime.now()
    today = _local_today(when)
    path = dated_briefing_path(today, user_id=user_id)
    publish_at = briefing_publish_at(db)
    published = path.exists()
    return {
        "publish_at": publish_at,
        "published": published,
        "date": today.isoformat(),
        "path": str(path) if published else None,
        "message": (
            f"Today's paper published at {publish_at}."
            if published
            else f"Publishes at {publish_at} — not ready yet."
        ),
    }


def maybe_publish_daily_briefing(db: Session, now: datetime | None = None) -> Path | None:
    from app.models import User

    when = now or datetime.now()
    hour, minute = (int(part) for part in briefing_publish_at(db).split(":"))
    if (when.hour, when.minute) < (hour, minute):
        for user in db.query(User).filter(User.active.is_(True)).all():
            prune_old_briefings(user_id=user.id)
        return None
    published: Path | None = None
    users = db.query(User).filter(User.active.is_(True)).all()
    if not users:
        if dated_briefing_path(when.date(), user_id=1).exists():
            return None
        return publish_daily_briefing(db, now=when, user_id=1)
    for user in users:
        if dated_briefing_path(when.date(), user_id=user.id).exists():
            continue
        published = publish_daily_briefing(db, now=when, user_id=user.id)
    return published


def publish_daily_briefing(
    db: Session,
    *,
    now: datetime | None = None,
    overwrite: bool = False,
    user_id: int | None = None,
) -> Path:
    uid = int(user_id or 1)
    when = now or datetime.now()
    day = _local_today(when)
    dest = dated_briefing_path(day, user_id=uid)
    created = overwrite or not dest.exists()
    if created:
        payload = current_briefing_payload(db, day="today", user_id=uid)
        payload["paper_date"] = day.isoformat()
        payload["date_label"] = format_paper_date(day, reader_date_format(db))
        payload["paper_title"] = paper_display_title(db, day)
        write_briefing_files(payload, stem=dated_stem(day), user_id=uid)
        write_category_briefing_files(db, payload, day, user_id=uid)
    prune_old_briefings(user_id=uid)
    if created:
        enqueue_latest_briefing(db, user_id=uid)
        from app.services import reader_config, reader_push

        if reader_config.reader_push_enabled(db, uid):
            reader_push.enqueue_frozen_briefing(db, user_id=uid)
        from app.services import ntfy

        paper_title = paper_display_title(db, day)
        instance = (settings.get_value(db, "instance_name") or "").strip() or "NewsCast"
        ntfy.notify(
            db,
            kind="publish",
            title=instance,
            body=f"Morning paper ready — {paper_title}",
            user_id=uid,
        )
    return dest


class _HtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip or tag in _DROP_TAGS:
            return
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "br":
            self.parts.append("<br/>")
            return
        if tag == "a":
            href = ""
            for key, value in attrs:
                if key == "href" and value and value.startswith(("http://", "https://", "/")):
                    href = html.escape(value, quote=True)
                    break
            self.parts.append(f'<a href="{href}">' if href else "<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag in _DROP_TAGS or tag not in _ALLOWED_TAGS or tag == "br":
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(html.escape(data))


def sanitize_html(value: str) -> str:
    raw = value or ""
    if "<" not in raw:
        return html.escape(raw)
    parser = _HtmlSanitizer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


def _story_body(summary: str) -> str:
    cleaned = sanitize_html(summary or "").strip()
    if not cleaned:
        return "<p></p>"
    if cleaned.lstrip().startswith("<"):
        return cleaned
    return f"<p>{cleaned}</p>"


def _payload_date_label(payload: dict) -> str:
    explicit = str(payload.get("date_label") or "").strip()
    if explicit:
        return explicit
    paper = str(payload.get("paper_date") or "").strip()
    if paper:
        try:
            return date.fromisoformat(paper[:10]).strftime("%d %b %Y")
        except ValueError:
            pass
    raw = str(payload.get("generated_at") or "")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone().strftime("%d %b %Y")
    except ValueError:
        return raw[:10]


def _payload_paper_date(payload: dict) -> str:
    paper = str(payload.get("paper_date") or "").strip()
    if paper:
        try:
            return date.fromisoformat(paper[:10]).isoformat()
        except ValueError:
            pass
    raw = str(payload.get("generated_at") or "")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone().date().isoformat()
    except ValueError:
        return _local_today().isoformat()


def group_stories(stories: list[dict], labels: dict[str, str] | None = None) -> list[tuple[str, str, list[dict]]]:
    names = labels or {}
    order = [SAVED_CATEGORY, *BUILTIN_LABELS.keys()]
    grouped: dict[str, list[dict]] = {}
    extras: list[str] = []
    for story in stories:
        key = story.get("category") or DEFAULT_CATEGORY
        if key not in grouped:
            grouped[key] = []
            if key not in order:
                extras.append(key)
        grouped[key].append(story)
    result: list[tuple[str, str, list[dict]]] = []
    for key in [*order, *extras]:
        items = grouped.get(key)
        if not items:
            continue
        if key == SAVED_CATEGORY:
            label = SAVED_CATEGORY_LABEL
        else:
            label = names.get(key) or BUILTIN_LABELS.get(key) or items[0].get("category_label") or key
        result.append((key, label, items))
    return result


def group_stories_by_source(stories: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: list[tuple[str, list[dict]]] = []
    index: dict[str, list[dict]] = {}
    for story in stories:
        source = (story.get("source") or "").strip() or "Unknown"
        bucket = index.get(source)
        if bucket is None:
            bucket = []
            index[source] = bucket
            groups.append((source, bucket))
        bucket.append(story)
    return groups


def stories_payload(
    stories: list[Story],
    instance_name: str = "",
    *,
    feeds: dict[str, Feed] | None = None,
    labels: dict[str, str] | None = None,
) -> dict:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    title = briefing_title(instance_name)
    feed_map = feeds or {}
    names = labels or {}
    items = []
    for story in stories:
        saved = bool(getattr(story, "saved", False))
        if saved:
            category = SAVED_CATEGORY
            category_label = SAVED_CATEGORY_LABEL
        else:
            feed = feed_map.get(story.source_name)
            category = getattr(feed, "category", None) or DEFAULT_CATEGORY
            category_label = names.get(category) or BUILTIN_LABELS.get(category, category)
        items.append(
            {
                "id": str(story.id),
                "title": story.title,
                "summary": story.summary,
                "source": story.source_name,
                "url": story.canonical_url,
                "published_at": (
                    story.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if story.published_at
                    else None
                ),
                "published_label": format_published(story.published_at or story.created_at),
                "category": category,
                "category_label": category_label,
                "saved": saved,
            }
        )
    return {
        "title": title,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "device": "xteink-x3",
        "stories": items,
    }


def render_txt(payload: dict) -> str:
    lines = [payload.get("title") or "NewsCast briefing", payload["generated_at"], ""]
    stories = payload.get("stories") or []
    lines.append(masthead_line(stories))
    lines.append("")
    if not stories:
        lines.append("No stories yet. Refresh from the NewsCast UI.")
        return "\n".join(lines) + "\n"
    index = 1
    for _key, label, group in group_stories(stories):
        lines.append(label)
        lines.append("")
        for source, source_stories in group_stories_by_source(group):
            lines.append(source)
            lines.append("")
            for story in source_stories:
                lines.append(f"{index}. {story['title']}")
                if story.get("published_label"):
                    lines.append(story["published_label"])
                lines.append(story["summary"])
                if story.get("url"):
                    lines.append(story["url"])
                lines.append("")
                index += 1
    return "\n".join(lines).rstrip() + "\n"


def write_epub(payload: dict, dest: Path) -> None:
    book = epub.EpubBook()
    heading = payload.get("title") or "NewsCast briefing"
    date_label = _payload_date_label(payload)
    paper_day = _payload_paper_date(payload)
    paper_title = (payload.get("paper_title") or "").strip() or f"{heading} {paper_day}"
    stories = payload.get("stories") or []
    book.set_identifier((payload.get("paper_id") or "").strip() or f"newscast-{paper_day}")
    book.set_title(paper_title)
    book.set_language("en")
    book.add_author("NewsCast")

    cover_bytes = render_newspaper_cover(heading=heading, date_label=date_label, stories=stories)
    book.set_cover("cover.jpg", cover_bytes, create_page=False)

    style = epub.EpubItem(uid="style", file_name="style/eink.css", media_type="text/css", content=EINK_CSS.encode())
    book.add_item(style)

    groups = group_stories(stories)
    cover_bits = [
        '<div class="cover-image"><img alt="Front page" src="cover.jpg"/></div>',
        f"<h1>{html.escape(heading)}</h1>",
        f'<p class="meta">{html.escape(date_label)}</p>',
    ]
    if stories:
        cover_bits.append(f'<p class="masthead">{html.escape(masthead_line(stories))}</p>')
        cover_bits.append('<hr class="cover-rule"/>')
        cover_bits.append('<div class="contents"><h2>Contents</h2>')
        for _key, label, group in groups:
            cover_bits.append(f'<div class="toc-category"><h3>{html.escape(label)}</h3>')
            for source, source_stories in group_stories_by_source(group):
                cover_bits.append(f'<div class="toc-source"><h4>{html.escape(source)}</h4>')
                cover_bits.append('<ol class="toc-stories">')
                for story in source_stories:
                    title = story.get("title") or "Untitled"
                    blurb = digest_blurb(story.get("summary") or "")
                    cover_bits.append("<li>")
                    cover_bits.append(f'<span class="toc-title">{html.escape(title)}</span>')
                    if blurb:
                        cover_bits.append(f'<span class="toc-digest">{html.escape(blurb)}</span>')
                    cover_bits.append("</li>")
                cover_bits.append("</ol></div>")
            cover_bits.append("</div>")
        cover_bits.append("</div>")
    else:
        cover_bits.append(f'<p class="masthead">{html.escape(masthead_line(stories))}</p>')
        cover_bits.append("<p>No stories yet.</p>")

    cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", lang="en")
    cover.content = "".join(cover_bits)
    cover.add_item(style)
    cover.add_link(href="style/eink.css", rel="stylesheet", type="text/css")
    book.add_item(cover)

    chapters = [cover]
    toc: list = []
    index = 1
    for _key, label, group in groups:
        source_sections = []
        for source, source_stories in group_stories_by_source(group):
            source_chapters = []
            for story in source_stories:
                title = story.get("title") or "Untitled"
                chapter = epub.EpubHtml(title=title[:120], file_name=f"story-{index}.xhtml", lang="en")
                byline_bits = [bit for bit in (story.get("source"), story.get("published_label")) if bit]
                byline = (
                    f'<p class="byline">{html.escape(" · ".join(byline_bits))}</p>' if byline_bits else ""
                )
                url = story.get("url") or ""
                link = (
                    f'<p class="original"><a href="{html.escape(url, quote=True)}">Original article</a></p>'
                    if url
                    else ""
                )
                chapter.content = (
                    f'<div class="story"><h1>{html.escape(title)}</h1>{byline}'
                    f'<div class="body">{_story_body(story.get("summary") or "")}</div>{link}</div>'
                )
                chapter.add_item(style)
                chapter.add_link(href="style/eink.css", rel="stylesheet", type="text/css")
                book.add_item(chapter)
                chapters.append(chapter)
                source_chapters.append(chapter)
                index += 1
            if source_chapters:
                source_sections.append((epub.Section(source), tuple(source_chapters)))
        if source_sections:
            toc.append((epub.Section(label), tuple(source_sections)))

    book.toc = toc or [cover]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    dest.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(dest), book)


def write_briefing_files(
    payload: dict,
    stem: str = "news",
    user_id: int | None = None,
) -> dict[str, Path]:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stem).strip("-") or "news"
    root = briefing_dir_for(user_id)
    txt_path = root / f"{safe}.txt"
    epub_path = root / f"{safe}.epub"
    txt_path.write_text(render_txt(payload), encoding="utf-8")
    write_epub(payload, epub_path)
    return {"txt": txt_path, "epub": epub_path}


def current_briefing_payload(db: Session, day: str | None = "today", user_id: int | None = None) -> dict:
    feeds = {feed.name: feed for feed in _scope_feeds(db.query(Feed), user_id).all()}
    return stories_payload(
        current_stories(db, day=day, user_id=user_id),
        settings.get_value(db, "instance_name"),
        feeds=feeds,
        labels=category_labels(db),
    )


def enqueue_latest_briefing(db: Session, user_id: int | None = None) -> SyncTask | None:
    uid = int(user_id or 1)
    fmt = (settings.get_value(db, "x3_briefing_format") or env.x3_briefing_format or "txt").lower()
    if fmt not in {"txt", "epub"}:
        fmt = "txt"
    path = frozen_briefing_path("today", suffix=fmt, fallback=False, user_id=uid)
    if path is None:
        return None
    day = date.fromisoformat(path.stem.removeprefix("news-"))
    return enqueue_sync_file(db, path, paper_download_name(db, day, suffix=fmt), user_id=uid)


def enqueue_sync_file(
    db: Session,
    path: Path,
    save_name: str,
    *,
    kind: str = "x3",
    save_path: str | None = None,
    user_id: int | None = None,
) -> SyncTask:
    uid = int(user_id or 1)
    device_id = settings.get_value(db, "x3_device_id") or env.x3_device_id or ""
    dest = save_path or (env.x3_save_path.rstrip("/") + "/" + save_name)
    kind_name = kind if kind in {"x3", "crosspoint"} else "x3"
    existing = (
        db.query(SyncTask)
        .filter(SyncTask.user_id == uid)
        .filter(SyncTask.kind == kind_name)
        .filter(SyncTask.status == "pending")
        .filter(SyncTask.save_path == dest)
        .first()
    )
    if existing:
        existing.file_path = str(path)
        existing.size = path.stat().st_size
        db.commit()
        db.refresh(existing)
        return existing
    task = SyncTask(
        user_id=uid,
        task_id=uuid.uuid4().hex,
        device_id=device_id,
        status="pending",
        kind=kind_name,
        file_path=str(path),
        save_path=dest,
        size=path.stat().st_size,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
