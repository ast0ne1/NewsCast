from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ebooklib import epub
from sqlalchemy import delete, func, or_
from sqlalchemy.orm import Session

from app.config import BRIEFING_DIR, env
from app.models import Story, SyncTask, utcnow
from app.services import settings

BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
MAX_BRIEFING_STORIES = 20


def retention_cutoff() -> datetime:
    return utcnow() - timedelta(days=env.story_retention_days)


def _story_age():
    return func.coalesce(Story.published_at, Story.created_at)


def _saved_still_current():
    now = utcnow()
    return or_(Story.expires_at.is_(None), Story.expires_at >= now)


def current_saved_stories(db: Session) -> list[Story]:
    return (
        db.query(Story)
        .filter(Story.saved.is_(True))
        .filter(_saved_still_current())
        .order_by(Story.created_at.desc())
        .all()
    )


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


def current_stories(db: Session, limit: int | None = None) -> list[Story]:
    if limit is None:
        limit = settings.briefing_limit(db)
    cutoff = retention_cutoff()
    age = _story_age()
    saved = current_saved_stories(db)
    favourites = (
        db.query(Story)
        .filter(Story.favourited.is_(True))
        .filter(or_(Story.saved.is_(False), Story.saved.is_(None)))
        .order_by(age.desc())
        .all()
    )
    recent = (
        db.query(Story)
        .filter(or_(Story.favourited.is_(False), Story.favourited.is_(None)))
        .filter(or_(Story.saved.is_(False), Story.saved.is_(None)))
        .filter(age >= cutoff)
        .order_by(age.desc())
        .limit(max(limit * 4, 40))
        .all()
    )
    recent = _diverse_recent(recent, limit)
    seen = {story.id for story in saved}
    stories = [*saved]
    for story in [*favourites, *recent]:
        if story.id not in seen:
            stories.append(story)
            seen.add(story.id)
    feed_stories = [story for story in stories if not story.saved]
    feed_stories.sort(key=lambda story: story.published_at or story.created_at or cutoff, reverse=True)
    return [*saved, *feed_stories]


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


def stories_payload(stories: list[Story], instance_name: str = "") -> dict:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    title = briefing_title(instance_name)
    return {
        "title": title,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "device": "xteink-x3",
        "stories": [
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
            }
            for story in stories
        ],
    }


def render_txt(payload: dict) -> str:
    lines = [payload.get("title") or "NewsCast briefing", payload["generated_at"], ""]
    if not payload["stories"]:
        lines.append("No stories yet. Refresh from the NewsCast UI.")
        return "\n".join(lines) + "\n"
    for index, story in enumerate(payload["stories"], start=1):
        lines.append(f"{index}. {story['title']}")
        if story.get("source"):
            lines.append(story["source"])
        if story.get("published_label"):
            lines.append(story["published_label"])
        lines.append(story["summary"])
        if story.get("url"):
            lines.append(story["url"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_epub(payload: dict, dest: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier(f"newscast-{payload['generated_at']}")
    heading = payload.get("title") or "NewsCast briefing"
    book.set_title(f"{heading} {payload['generated_at'][:10]}")
    book.set_language("en")
    book.add_author("NewsCast")

    chapters = []
    intro = epub.EpubHtml(title="Briefing", file_name="intro.xhtml", lang="en")
    heading = payload.get("title") or "NewsCast briefing"
    intro.content = (
        f"<h1>{heading}</h1><p>{payload['generated_at']}</p>"
        if payload["stories"]
        else f"<h1>{heading}</h1><p>No stories yet.</p>"
    )
    book.add_item(intro)
    chapters.append(intro)

    for index, story in enumerate(payload["stories"], start=1):
        chapter = epub.EpubHtml(
            title=story["title"][:80],
            file_name=f"story-{index}.xhtml",
            lang="en",
        )
        source = f"<p><em>{story['source']}</em></p>" if story.get("source") else ""
        published = f"<p>{story['published_label']}</p>" if story.get("published_label") else ""
        link = f'<p><a href="{story["url"]}">Original</a></p>' if story.get("url") else ""
        chapter.content = f"<h2>{story['title']}</h2>{source}{published}<p>{story['summary']}</p>{link}"
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    epub.write_epub(str(dest), book)


def write_briefing_files(payload: dict) -> dict[str, Path]:
    txt_path = BRIEFING_DIR / "news.txt"
    epub_path = BRIEFING_DIR / "news.epub"
    txt_path.write_text(render_txt(payload), encoding="utf-8")
    write_epub(payload, epub_path)
    return {"txt": txt_path, "epub": epub_path}


def current_briefing_payload(db: Session) -> dict:
    return stories_payload(current_stories(db), settings.get_value(db, "instance_name"))


def enqueue_latest_briefing(db: Session) -> SyncTask | None:
    payload = current_briefing_payload(db)
    files = write_briefing_files(payload)
    fmt = (settings.get_value(db, "x3_briefing_format") or env.x3_briefing_format or "txt").lower()
    if fmt not in {"txt", "epub"}:
        fmt = "txt"
    path = files[fmt]
    save_name = f"NewsCast-{payload['generated_at'][:10]}.{fmt}"
    return enqueue_sync_file(db, path, save_name)


def enqueue_sync_file(db: Session, path: Path, save_name: str) -> SyncTask:
    device_id = settings.get_value(db, "x3_device_id") or env.x3_device_id or ""
    save_path = env.x3_save_path.rstrip("/") + "/" + save_name
    task = SyncTask(
        task_id=uuid.uuid4().hex,
        device_id=device_id,
        status="pending",
        file_path=str(path),
        save_path=save_path,
        size=path.stat().st_size,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
