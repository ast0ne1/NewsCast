from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock, Thread

import feedparser
import httpx
import trafilatura
from sqlalchemy.orm import Session

from app.config import env
from app.db import SessionLocal
from app.models import Feed, Story, utcnow
from app.services import briefing, settings
from app.services.dedupe import canonicalize_url, cluster_key, content_hash, is_duplicate_title
from app.services.filters import feed_keyword_lists, story_passes_filters
from app.services.health import record_fetch
from app.services.schedule import feed_is_due, feed_is_muted
from app.services.scrape import (
    article_candidates,
    discover_rss,
    guess_feed_urls,
    looks_like_feed_url,
    published_from_url,
)

logger = logging.getLogger("newscast.ingest")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
PAGE_TIMEOUT = httpx.Timeout(25.0, connect=10.0)
RSS_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8,*/*;q=0.5"
)
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
MIN_EXCERPT_CHARS = 280
MAX_EXTRACTS_PER_FEED = 3
_lock = Lock()


class IngestState:
    running: bool = False
    progress: str = ""
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_new_stories: int = 0
    last_message: str = "Idle"


state = IngestState()


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _http_headers(accept: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-GB,en;q=0.9",
        "Cache-Control": "no-cache",
    }


def _http_get(
    url: str,
    *,
    accept: str | None = None,
    timeout: httpx.Timeout | None = None,
    status_out: list[int] | None = None,
    db: Session | None = None,
    fetch_cache: dict[str, tuple[str, int]] | None = None,
) -> str:
    key = (url or "").strip()
    if fetch_cache is not None and key in fetch_cache:
        body, code = fetch_cache[key]
        if status_out is not None:
            status_out.append(code)
        return body
    if db is not None:
        from app.services import source_cache

        try:
            body, code = source_cache.get_or_fetch_feed(
                db,
                key,
                accept=accept,
                timeout=timeout,
            )
            if fetch_cache is not None:
                fetch_cache[key] = (body, code)
            if status_out is not None:
                status_out.append(code)
            return body
        except Exception:
            pass
    with httpx.Client(
        timeout=timeout or HTTP_TIMEOUT,
        follow_redirects=True,
        headers=_http_headers(accept or RSS_ACCEPT),
    ) as client:
        response = client.get(url)
        if status_out is not None:
            status_out.append(response.status_code)
        response.raise_for_status()
        body = response.text
        if fetch_cache is not None:
            fetch_cache[key] = (body, response.status_code)
        return body


def _http_get_html(url: str, db: Session | None = None) -> str:
    if db is not None:
        from app.services import source_cache

        try:
            _title, excerpt = source_cache.get_or_fetch_article(db, url)
            if excerpt:
                # Prefer full HTML path for trafilatura; article cache stores excerpt only.
                pass
        except Exception:
            pass
    return _http_get(url, accept=HTML_ACCEPT, timeout=PAGE_TIMEOUT, db=db)


def _plain_text(value: str) -> str:
    text = (value or "").strip()
    if "<" not in text:
        return text
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_article(url: str, fallback: str, db: Session | None = None) -> str:
    fallback = _plain_text(fallback)
    if len(fallback) >= MIN_EXCERPT_CHARS:
        return fallback
    if db is not None:
        from app.services import source_cache

        try:
            _title, excerpt = source_cache.get_or_fetch_article(db, url)
            if excerpt and len(excerpt.strip()) >= MIN_EXCERPT_CHARS:
                return excerpt.strip()
            if excerpt:
                fallback = excerpt.strip() or fallback
        except Exception as exc:  # noqa: BLE001
            logger.debug("cached extract failed for %s: %s", url, exc)
    try:
        html = _http_get_html(url, db=db)
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text:
            return text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract failed for %s: %s", url, exc)
    return fallback


def _existing_lookup(
    db: Session,
    user_id: int | None = None,
) -> tuple[set[str], set[str], list[tuple[str, datetime | None]]]:
    query = db.query(Story)
    if user_id is not None:
        query = query.filter(Story.user_id == user_id)
    stories = query.all()
    urls = {story.canonical_url for story in stories}
    hashes = {story.content_hash for story in stories}
    titles = [(story.title, story.published_at or story.created_at) for story in stories]
    return urls, hashes, titles


def _same_day(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return True
    return left.astimezone(timezone.utc).date() == right.astimezone(timezone.utc).date()


def _is_known(title: str, published_at: datetime | None, url: str, digest: str, urls, hashes, titles) -> bool:
    if url in urls or digest in hashes:
        return True
    for existing_title, existing_when in titles:
        if _same_day(published_at, existing_when) and is_duplicate_title(title, existing_title):
            return True
    return False


def _items_from_parsed(parsed, feed: Feed) -> list[dict]:
    items = []
    for entry in parsed.entries[: env.max_stories_per_feed]:
        link = canonicalize_url(getattr(entry, "link", "") or "")
        title = (getattr(entry, "title", "") or "").strip()
        if not link or not title:
            continue
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        published = _parse_date(getattr(entry, "published", None) or getattr(entry, "updated", None))
        items.append(
            {
                "title": title,
                "url": link,
                "excerpt": summary,
                "published_at": published,
                "source": feed.name,
            }
        )
    return items


def _items_from_scrape(page_url: str, page_html: str, feed: Feed) -> list[dict]:
    items = []
    for candidate in article_candidates(page_url, page_html, limit=env.max_stories_per_feed):
        url = canonicalize_url(candidate["url"])
        items.append(
            {
                "title": candidate["title"],
                "url": url,
                "excerpt": "",
                "published_at": published_from_url(url) or utcnow(),
                "source": feed.name,
            }
        )
    return items


def _try_rss_url(
    url: str,
    feed: Feed,
    status_out: list[int] | None = None,
    *,
    db: Session | None = None,
    fetch_cache: dict[str, tuple[str, int]] | None = None,
) -> list[dict]:
    try:
        return _items_from_parsed(
            feedparser.parse(_http_get(url, status_out=status_out, db=db, fetch_cache=fetch_cache)),
            feed,
        )
    except httpx.HTTPStatusError as exc:
        if status_out is not None:
            status_out.append(exc.response.status_code)
        logger.debug("rss fetch failed for %s (%s): %s", feed.name, url, exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.debug("rss fetch failed for %s (%s): %s", feed.name, url, exc)
        return []


def _collect_feed_items(
    feed: Feed,
    *,
    db: Session | None = None,
    fetch_cache: dict[str, tuple[str, int]] | None = None,
) -> tuple[list[dict], int | None]:
    status_out: list[int] = []
    mode = (feed.type or "auto").lower()
    if mode == "rss" or looks_like_feed_url(feed.url):
        direct = _try_rss_url(feed.url, feed, status_out, db=db, fetch_cache=fetch_cache)
        if direct:
            return direct, status_out[-1] if status_out else 200
        if mode == "rss":
            raise RuntimeError("No RSS entries found")
    if mode in {"auto", "webpage"}:
        for guessed in guess_feed_urls(feed.url):
            found = _try_rss_url(guessed, feed, status_out, db=db, fetch_cache=fetch_cache)
            if found:
                return found, status_out[-1] if status_out else 200
    homepage_error = None
    body = ""
    try:
        body = _http_get(feed.url, status_out=status_out, db=db, fetch_cache=fetch_cache)
    except Exception as exc:  # noqa: BLE001
        homepage_error = exc
    else:
        discovered = discover_rss(feed.url, body)
        if discovered and discovered.rstrip("/") != feed.url.rstrip("/"):
            found = _try_rss_url(discovered, feed, status_out, db=db, fetch_cache=fetch_cache)
            if found:
                return found, status_out[-1] if status_out else 200
        scraped = _items_from_scrape(feed.url, body, feed)
        if scraped:
            return scraped, status_out[-1] if status_out else 200
        page_rss = _items_from_parsed(feedparser.parse(body), feed)
        if page_rss:
            return page_rss, status_out[-1] if status_out else 200
    if homepage_error:
        raise homepage_error
    raise RuntimeError("No RSS entries or scrapeable articles found")


def _unpack_collect(result) -> tuple[list[dict], int | None]:
    if isinstance(result, tuple):
        items, status = result
        return list(items), status
    return list(result), 200


def _purge_old_stories(db: Session) -> None:
    briefing.purge_expired_stories(db)


def _backfill_translations(db: Session, source_names: list[str] | None = None) -> int:
    from app.services.translate import (
        needs_translation,
        resolve_provider,
        translate_story,
        translate_text,
        translate_target_lang,
    )

    query = db.query(Feed).filter(Feed.translate.is_(True))
    if source_names is not None:
        if not source_names:
            return 0
        query = query.filter(Feed.name.in_(source_names))
    feeds = query.all()
    if not feeds:
        return 0
    llm = settings.llm_config(db)
    global_provider = settings.translate_provider(db)
    target = translate_target_lang(db)
    by_name = {feed.name: feed for feed in feeds}
    updated = 0
    stories = db.query(Story).filter(Story.source_name.in_(list(by_name))).all()
    for story in stories:
        if not needs_translation(story, target):
            continue
        feed = by_name.get(story.source_name)
        provider = resolve_provider(db, feed, global_provider=global_provider)
        if not provider:
            continue
        original_excerpt = story.raw_excerpt or ""
        original_summary = story.summary or ""
        try:
            title, excerpt = translate_story(
                story.title,
                original_excerpt or original_summary,
                provider=provider,
                target_lang=target,
                config=llm,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill translate failed for %s: %s", story.title, exc)
            continue
        if title == story.title and excerpt in {original_excerpt, original_summary, ""} and story.content_lang == target:
            continue
        story.title = title
        if excerpt:
            story.raw_excerpt = excerpt[:4000]
        if not original_summary.strip():
            story.summary = excerpt or title
        elif original_summary.strip() == original_excerpt.strip():
            story.summary = excerpt or title
        else:
            story.summary = (
                translate_text(original_summary, provider=provider, target_lang=target, config=llm, db=db)
                or excerpt
                or title
            )
        story.content_lang = target
        story.content_hash = content_hash(story.title, story.raw_excerpt or "")
        story.cluster_key = cluster_key(story.title)
        updated += 1
        time.sleep(0.35)
    return updated


def run_ingest(db: Session, force: bool = True, feed_id: int | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "A refresh is already running."}

    state.running = True
    state.progress = "Starting"
    state.last_started_at = utcnow()
    state.last_error = None
    created = 0

    try:
        llm = settings.llm_config(db)
        global_minutes = settings.get_int(db, "ingest_interval_minutes", env.ingest_interval_minutes)
        active_start = settings.get_value(db, "ingest_active_start")
        active_end = settings.get_value(db, "ingest_active_end")
        urls_by_user: dict[int, set[str]] = {}
        hashes_by_user: dict[int, set[str]] = {}
        titles_by_user: dict[int, list[tuple[str, datetime | None]]] = {}
        fetch_cache: dict[str, tuple[str, int]] = {}

        def _lookup_for(uid: int):
            if uid not in urls_by_user:
                urls, hashes, titles = _existing_lookup(db, uid)
                urls_by_user[uid] = urls
                hashes_by_user[uid] = hashes
                titles_by_user[uid] = titles
            return urls_by_user[uid], hashes_by_user[uid], titles_by_user[uid]

        if feed_id is not None:
            feed = db.get(Feed, feed_id)
            if feed is None:
                state.last_message = "Feed not found."
                state.last_new_stories = 0
                return {"ok": False, "created": 0, "message": state.last_message}
            feeds = [feed]
        else:
            feeds = db.query(Feed).filter(Feed.enabled.is_(True)).all()
            feeds = [feed for feed in feeds if not feed_is_muted(feed)]
            if not force:
                feeds = [
                    feed
                    for feed in feeds
                    if feed_is_due(
                        feed,
                        global_minutes,
                        active_start=active_start,
                        active_end=active_end,
                    )
                ]
        global_include = settings.get_value(db, "keyword_include")
        global_exclude = settings.get_value(db, "keyword_exclude")
        global_translate = settings.translate_provider(db)
        if not feeds:
            state.last_message = "No enabled feeds due yet." if not force else "No enabled feeds."
            state.last_new_stories = 0
            return {"ok": True, "created": 0, "message": state.last_message}

        candidates: list[dict] = []
        for feed in feeds:
            state.progress = feed.name
            uid = int(getattr(feed, "user_id", None) or 1)
            urls, hashes, titles = _lookup_for(uid)
            try:
                try:
                    collected = _collect_feed_items(feed, db=db, fetch_cache=fetch_cache)
                except TypeError:
                    collected = _collect_feed_items(feed)
                items, status_code = _unpack_collect(collected)
                feed.last_fetched_at = utcnow()
                feed.last_error = None
                record_fetch(feed, status_code=status_code or 200, item_count=len(items))
                extracts = 0
                summarize_feed = bool(getattr(feed, "summarize", True))
                translate_feed = bool(getattr(feed, "translate", False))
                include, exclude = feed_keyword_lists(feed, global_include, global_exclude)
                for item in items:
                    item["excerpt"] = _plain_text(item.get("excerpt") or "")
                    item["summarize"] = summarize_feed
                    item["user_id"] = uid
                    digest = content_hash(item["title"], item["excerpt"])
                    if _is_known(item["title"], item["published_at"], item["url"], digest, urls, hashes, titles):
                        continue
                    should_extract = (not summarize_feed) or (
                        extracts < MAX_EXTRACTS_PER_FEED and len(item["excerpt"]) < MIN_EXCERPT_CHARS
                    )
                    if should_extract:
                        item["excerpt"] = _extract_article(item["url"], item["excerpt"], db=db)
                        if summarize_feed:
                            extracts += 1
                    if translate_feed:
                        from app.services.translate import resolve_provider, translate_story, translate_target_lang

                        provider = resolve_provider(db, feed, global_provider=global_translate)
                        target = translate_target_lang(db)
                        try:
                            item["title"], item["excerpt"] = translate_story(
                                item["title"],
                                item["excerpt"],
                                provider=provider or "google",
                                target_lang=target,
                                config=llm,
                                db=db,
                            )
                            item["content_lang"] = target
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("translate failed for %s: %s", item["title"], exc)
                    item["content_hash"] = content_hash(item["title"], item["excerpt"])
                    item["cluster_key"] = cluster_key(item["title"])
                    if not story_passes_filters(item["title"], item.get("excerpt") or "", "", include, exclude):
                        continue
                    if _is_known(
                        item["title"],
                        item["published_at"],
                        item["url"],
                        item["content_hash"],
                        urls,
                        hashes,
                        titles,
                    ):
                        continue
                    candidates.append(item)
                    urls.add(item["url"])
                    hashes.add(item["content_hash"])
                    titles.append((item["title"], item["published_at"]))
            except httpx.HTTPStatusError as exc:
                feed.last_error = str(exc)[:500]
                record_fetch(feed, status_code=exc.response.status_code, item_count=0)
                logger.warning("feed %s failed: %s", feed.name, exc)
            except Exception as exc:  # noqa: BLE001
                feed.last_error = str(exc)[:500]
                status = getattr(feed, "last_status_code", None)
                if isinstance(exc, RuntimeError):
                    status = status or 200
                record_fetch(feed, status_code=status, item_count=0)
                logger.warning("feed %s failed: %s", feed.name, exc)
            db.add(feed)

        clusters: dict[str, dict] = {}
        for item in candidates:
            matched = None
            cluster_namespace = f"{item.get('user_id', 1)}:"
            for key, chosen in clusters.items():
                if not key.startswith(cluster_namespace):
                    continue
                if is_duplicate_title(item["title"], chosen["title"]):
                    matched = key
                    break
            if matched:
                if len(item.get("excerpt") or "") > len(clusters[matched].get("excerpt") or ""):
                    item["cluster_key"] = clusters[matched]["cluster_key"]
                    clusters[matched] = item
                continue
            clusters[f"{cluster_namespace}{item['cluster_key'] or item['url']}"] = item

        from app.services.importance import score_importance
        from app.services.summarize import summarize_with_config

        feed_by_name = {feed.name: feed for feed in feeds}
        for item in clusters.values():
            excerpt = item.get("excerpt") or ""
            if item.get("summarize", True):
                try:
                    summary = summarize_with_config(item["title"], excerpt, item["source"], llm, db=db)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("summarize failed for %s: %s", item["title"], exc)
                    from app.services.summarize import fallback_summary

                    summary = fallback_summary(item["title"], excerpt)
            else:
                summary = excerpt.strip() or item["title"]
            feed = feed_by_name.get(item["source"])
            category = getattr(feed, "category", None) or "news"
            importance = score_importance(
                item["title"],
                excerpt,
                source=item["source"],
                category=category,
                config=llm,
                db=db,
            )
            db.add(
                Story(
                    user_id=int(item.get("user_id") or 1),
                    title=item["title"],
                    summary=summary,
                    source_name=item["source"],
                    canonical_url=item["url"],
                    published_at=item["published_at"],
                    content_hash=item["content_hash"],
                    cluster_key=item["cluster_key"],
                    raw_excerpt=excerpt[:4000],
                    importance=importance,
                    content_lang=item.get("content_lang"),
                )
            )
            created += 1

        if feed_id is not None:
            translated = _backfill_translations(
                db,
                source_names=[feed.name for feed in feeds if getattr(feed, "translate", False)],
            )
        else:
            translated = _backfill_translations(db)
        _purge_old_stories(db)
        db.commit()
        if feed_id is not None:
            message = f"Updated {feeds[0].name}. Added {created} new stor{'y' if created == 1 else 'ies'}."
        else:
            message = f"Added {created} new stor{'y' if created == 1 else 'ies'}."
        if translated:
            from app.services.translate import target_language_name, translate_target_lang

            lang_name = target_language_name(translate_target_lang(db))
            message += (
                f" Translated {translated} existing stor{'y' if translated == 1 else 'ies'} into {lang_name}."
            )
        if not llm.ready:
            if llm.provider == "ollama":
                message += " Ollama has no model set, so summaries used extracted text."
            else:
                message += " OpenAI key is not set, so summaries used extracted text."
        state.last_new_stories = created
        state.last_message = message
        state.last_error = None
        return {"ok": True, "created": created, "message": message, "llm_ready": llm.ready}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        state.last_error = str(exc)
        state.last_message = "Refresh failed."
        logger.exception("ingest failed")
        return {"ok": False, "message": str(exc)}
    finally:
        state.running = False
        state.progress = ""
        state.last_finished_at = utcnow()
        _lock.release()


def start_ingest(force: bool = True, feed_id: int | None = None) -> dict:
    if state.running:
        return {"ok": True, "message": "A refresh is already running.", "running": True}

    started = "Refresh started."
    if feed_id is not None:
        db = SessionLocal()
        try:
            feed = db.get(Feed, feed_id)
            if feed is None:
                return {"ok": False, "message": "Feed not found.", "running": False}
            started = f"Updating {feed.name}."
        finally:
            db.close()

    def _worker() -> None:
        db = SessionLocal()
        try:
            run_ingest(db, force=force, feed_id=feed_id)
        finally:
            db.close()

    Thread(target=_worker, daemon=True, name="newscast-ingest").start()
    return {"ok": True, "message": started, "running": True}


def snapshot() -> dict:
    return {
        "running": state.running,
        "progress": state.progress,
        "last_started_at": state.last_started_at.isoformat() if state.last_started_at else None,
        "last_finished_at": state.last_finished_at.isoformat() if state.last_finished_at else None,
        "last_error": state.last_error,
        "last_new_stories": state.last_new_stories,
        "last_message": state.last_message,
    }
