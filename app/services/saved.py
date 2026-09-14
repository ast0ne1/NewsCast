from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import trafilatura
from sqlalchemy.orm import Session

from app.models import Story, utcnow
from app.services.dedupe import canonicalize_url, cluster_key, content_hash
from app.services.ingest import _http_get_html

KEEP_OPTIONS = [
    (7, "7 days"),
    (14, "14 days"),
    (30, "30 days"),
    (0, "Pick a date"),
]
ORIGIN_BRIEFING = "briefing"
ORIGIN_MANUAL = "manual"
ORIGIN_LABELS = {
    ORIGIN_BRIEFING: "From Briefing",
    ORIGIN_MANUAL: "Added URL",
}


def normalize_saved_origin(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in ORIGIN_LABELS else ORIGIN_MANUAL


def saved_origin_label(value: str | None) -> str:
    key = (value or "").strip().lower()
    return ORIGIN_LABELS.get(key, "Saved")


def parse_expiry(keep_days: str, custom_date: str) -> datetime:
    now = utcnow()
    if keep_days == "0":
        raw = (custom_date or "").strip()
        if not raw:
            raise ValueError("Choose a date, or keep the article for 7 days.")
        try:
            expires = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("That expiry date is not valid.") from exc
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc, hour=23, minute=59)
        if expires <= now:
            raise ValueError("Expiry must be in the future.")
        return expires
    try:
        days = int(keep_days or "7")
    except ValueError as exc:
        raise ValueError("Choose how long to keep the article.") from exc
    if days < 1:
        raise ValueError("Choose how long to keep the article.")
    return now + timedelta(days=days)


def _title_from_html(page_html: str, url: str) -> str:
    try:
        meta = trafilatura.extract_metadata(page_html)
        if meta and getattr(meta, "title", None):
            return str(meta.title).strip()
    except Exception:  # noqa: BLE001
        pass
    host = urlparse(url).netloc.removeprefix("www.")
    return host or url


def normalize_article_url(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw and not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").strip()
    if parsed.scheme not in {"http", "https"} or not host or " " in host:
        raise ValueError("Enter a full article URL, such as https://example.com/story")
    return raw


def save_article(
    db: Session,
    url: str,
    keep_days: str = "7",
    custom_date: str = "",
    *,
    origin: str = ORIGIN_MANUAL,
) -> Story:
    raw = normalize_article_url(url)
    canonical = canonicalize_url(raw)
    expires_at = parse_expiry(keep_days, custom_date)
    saved_origin = normalize_saved_origin(origin)
    try:
        page_html = _http_get_html(raw)
    except httpx.TimeoutException as exc:
        raise ValueError("That page took too long to load. Try again.") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 403:
            raise ValueError("That site blocked the scrape. Try another article URL.") from exc
        if code == 404:
            raise ValueError("That page was not found. Check the URL.") from exc
        raise ValueError(f"Could not open that page ({code}).") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Could not open that page. Check the URL, or try a different article.") from exc
    text = trafilatura.extract(page_html, include_comments=False, include_tables=False)
    text = (text or "").strip()
    if len(text) < 80:
        raise ValueError("Could not read the full article from that page. Some sites block scrapes.")
    title = _title_from_html(page_html, canonical)
    host = urlparse(canonical).netloc.removeprefix("www.") or "Saved"
    existing = db.query(Story).filter(Story.canonical_url == canonical).one_or_none()
    if existing:
        existing.title = title[:500]
        existing.summary = text
        existing.source_name = host
        existing.raw_excerpt = text
        existing.content_hash = content_hash(title, text)
        existing.cluster_key = cluster_key(title)
        existing.saved = True
        existing.saved_origin = saved_origin
        existing.expires_at = expires_at
        if existing.importance is None:
            existing.importance = 3
        db.commit()
        db.refresh(existing)
        _capture_favicon(canonical)
        return existing
    story = Story(
        title=title[:500],
        summary=text,
        source_name=host,
        canonical_url=canonical,
        published_at=utcnow(),
        content_hash=content_hash(title, text),
        cluster_key=cluster_key(title),
        raw_excerpt=text,
        favourited=False,
        saved=True,
        saved_origin=saved_origin,
        expires_at=expires_at,
        importance=3,
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    _capture_favicon(canonical)
    return story


def _capture_favicon(url: str) -> None:
    try:
        from app.services.favicon import ensure_favicon

        ensure_favicon(url)
    except Exception:  # noqa: BLE001
        pass
