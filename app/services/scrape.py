from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import re

from lxml import html

COMMON_FEED_PATHS = ("/feed/", "/rss.xml", "/rss")
DATE_PATH = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")

SKIP_PATHS = {
    "",
    "rss",
    "feed",
    "feeds",
    "atom",
    "login",
    "signin",
    "signup",
    "register",
    "about",
    "contact",
    "privacy",
    "terms",
    "search",
    "tag",
    "tags",
    "category",
    "author",
    "wp-login.php",
    "cart",
    "account",
}
SKIP_EXT = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".pdf", ".zip", ".mp4"}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def discover_rss(page_url: str, page_html: str) -> str | None:
    try:
        tree = html.fromstring(page_html)
    except Exception:
        tree = None
    if tree is not None:
        for link in tree.xpath("//link[@rel='alternate']"):
            kind = (link.get("type") or "").lower()
            if "rss" in kind or "atom" in kind or "xml" in kind:
                href = (link.get("href") or "").strip()
                if href:
                    return urljoin(page_url, href)
    for candidate in guess_feed_urls(page_url):
        return candidate
    return None


def guess_feed_urls(page_url: str) -> list[str]:
    parsed = urlparse(page_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    found: list[str] = []
    for path in COMMON_FEED_PATHS:
        candidate = root + path
        if candidate.rstrip("/") != page_url.rstrip("/"):
            found.append(candidate)
    return found


def looks_like_feed_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(token in path for token in ("/feed", "/rss", "/atom", ".xml", ".rss", ".atom"))


def published_from_url(url: str) -> datetime | None:
    match = DATE_PATH.search(urlparse(url).path)
    if not match:
        return None
    try:
        return datetime(int(match[1]), int(match[2]), int(match[3]), tzinfo=timezone.utc)
    except ValueError:
        return None


def article_candidates(page_url: str, page_html: str, limit: int = 12) -> list[dict]:
    try:
        tree = html.fromstring(page_html)
    except Exception:
        return []
    host = _host(page_url)
    seen: set[str] = set()
    found: list[dict] = []
    for anchor in tree.xpath("//a[@href]"):
        href = urljoin(page_url, anchor.get("href") or "")
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if _host(href) != host:
            continue
        path = parsed.path.rstrip("/")
        first = path.lstrip("/").split("/", 1)[0].lower()
        if first in SKIP_PATHS:
            continue
        if any(parsed.path.lower().endswith(ext) for ext in SKIP_EXT):
            continue
        title = " ".join((anchor.text_content() or "").split())
        if len(title) < 16:
            continue
        clean = href.split("#", 1)[0]
        if clean in seen or clean.rstrip("/") == page_url.rstrip("/"):
            continue
        seen.add(clean)
        found.append({"title": title, "url": clean})
        if len(found) >= limit:
            break
    return found
