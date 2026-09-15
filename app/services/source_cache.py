from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
import trafilatura
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.models import ArticleCache, SourceFetch, utcnow

logger = logging.getLogger("newscast.source_cache")

CACHE_DIR = DATA_DIR / "cache"
FEED_CACHE_DIR = CACHE_DIR / "feeds"
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
MAX_INLINE_BODY = 400_000


def _headers(accept: str, *, etag: str | None = None, last_modified: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-GB,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def _url_key(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()


def _feed_body_path(url: str) -> Path:
    FEED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return FEED_CACHE_DIR / f"{_url_key(url)}.txt"


def _read_cached_body(row: SourceFetch) -> str | None:
    if row.body:
        return row.body
    if row.body_path:
        path = Path(row.body_path)
        if not path.is_absolute():
            path = DATA_DIR / path
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def get_or_fetch_feed(
    db: Session,
    url: str,
    *,
    accept: str | None = None,
    force: bool = False,
    timeout: httpx.Timeout | None = None,
) -> tuple[str, int]:
    """Return (body, status_code), reusing SourceFetch when possible."""
    key = (url or "").strip()
    row = db.get(SourceFetch, key)
    if row and not force:
        body = _read_cached_body(row)
        if body is not None:
            return body, int(row.status_code or 200)

    etag = row.etag if row else None
    last_modified = row.last_modified if row else None
    with httpx.Client(
        timeout=timeout or HTTP_TIMEOUT,
        follow_redirects=True,
        headers=_headers(accept or RSS_ACCEPT, etag=etag, last_modified=last_modified),
    ) as client:
        response = client.get(key)
        if response.status_code == 304 and row:
            body = _read_cached_body(row)
            if body is not None:
                row.fetched_at = utcnow()
                db.add(row)
                db.commit()
                return body, int(row.status_code or 200)
        response.raise_for_status()
        body = response.text

    path = _feed_body_path(key)
    if len(body) > MAX_INLINE_BODY:
        path.write_text(body, encoding="utf-8")
        body_path = str(path.relative_to(DATA_DIR)).replace("\\", "/")
        inline = None
    else:
        body_path = None
        inline = body
        if path.exists():
            path.unlink()

    if row is None:
        row = SourceFetch(url=key)
    row.body = inline
    row.body_path = body_path
    row.etag = response.headers.get("etag")
    row.last_modified = response.headers.get("last-modified")
    row.fetched_at = utcnow()
    row.status_code = response.status_code
    db.add(row)
    db.commit()
    return body, response.status_code


def get_or_fetch_article(db: Session, url: str, *, force: bool = False) -> tuple[str, str]:
    """Return (title, excerpt) from ArticleCache or by extracting the page."""
    key = (url or "").strip()
    row = db.get(ArticleCache, key)
    if row and not force and (row.excerpt or "").strip():
        return row.title or "", row.excerpt or ""

    title = (row.title if row else "") or ""
    excerpt = ""
    try:
        with httpx.Client(
            timeout=PAGE_TIMEOUT,
            follow_redirects=True,
            headers=_headers(HTML_ACCEPT),
        ) as client:
            response = client.get(key)
            response.raise_for_status()
            html = response.text
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text:
            excerpt = text.strip()
        meta_title = trafilatura.extract_metadata(html)
        if meta_title and meta_title.title:
            title = meta_title.title.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("article extract failed for %s: %s", key, exc)

    if row is None:
        row = ArticleCache(canonical_url=key)
    row.title = title[:500]
    row.excerpt = excerpt[:20000]
    row.fetched_at = utcnow()
    db.add(row)
    db.commit()
    return row.title, row.excerpt
