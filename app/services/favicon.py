from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from urllib.parse import urljoin, urlparse

import httpx

from app.config import BUNDLED_FAVICON_DIR, FAVICON_DIR

logger = logging.getLogger("newscast.favicon")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
MAX_BYTES = 256 * 1024
ATTEMPT_COOLDOWN = timedelta(days=7)
FEED_HOST_PREFIXES = ("feeds.", "rss.", "feed.")
# Feed hosts that are not the public site (BBC RSS lives on bbci.co.uk).
SITE_ALIASES = {
    "bbci.co.uk": ("https://www.bbc.com", "https://www.bbc.co.uk"),
    "api.sr.se": ("https://www.sverigesradio.se",),
}
ICON_HELPERS = (
    "https://icons.duckduckgo.com/ip3/{host}.ico",
    "https://www.google.com/s2/favicons?domain={host}&sz=64",
)
ICON_LINK_RE = re.compile(
    r"<link\b[^>]*\brel=['\"][^'\"]*(?:icon|apple-touch-icon)[^'\"]*['\"][^>]*>",
    re.I,
)
HREF_RE = re.compile(r"""\bhref=['"]([^'"]+)['"]""", re.I)
FEED_IMAGE_RE = re.compile(
    r"<(?:url|icon|logo)\s*>([^<]+)</(?:url|icon|logo)>|"
    r"<(?:itunes:image|media:thumbnail|media:content|image)\b[^>]*\b(?:href|url)=['\"]([^'\"]+)",
    re.I,
)
RSS_LINK_RE = re.compile(r"<link(?:\s[^>]*)?>([^<]+)</link>", re.I)
ATOM_LINK_RE = re.compile(r"""<link\b[^>]*\bhref=['"]([^'"]+)['"][^>]*>""", re.I)
ITEM_SPLIT_RE = re.compile(r"<(?:item|entry)\b", re.I)

FAVICON_DIR.mkdir(parents=True, exist_ok=True)


def seed_bundled_favicons() -> None:
    if not BUNDLED_FAVICON_DIR.is_dir():
        return
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    for src in BUNDLED_FAVICON_DIR.iterdir():
        if not src.is_file() or not _file_is_usable(src):
            continue
        dest = FAVICON_DIR / src.name
        if dest.exists() and _file_is_usable(dest):
            continue
        try:
            shutil.copyfile(src, dest)
        except OSError:
            logger.debug("could not seed bundled favicon %s", src.name, exc_info=True)


def host_key(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.")[:80]


def homepage_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    for prefix in FEED_HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    return f"{scheme}://{host}" if host else ""


def website_candidates(url: str) -> list[str]:
    found: list[str] = []
    home = homepage_url(url)
    host = host_key(home) if home else ""
    found.extend(SITE_ALIASES.get(host, ()))
    if home and host not in SITE_ALIASES:
        found.append(home)
        parsed = urlparse(home)
        if parsed.hostname and not (parsed.hostname or "").lower().startswith("www."):
            found.append(f"{parsed.scheme}://www.{parsed.hostname}")
    return _unique(found)


def cached_src(filename: str | None) -> str | None:
    if not filename:
        return None
    safe = Path(filename).name
    path = FAVICON_DIR / safe
    if path.is_file() and _file_is_usable(path):
        return f"/favicons/{safe}"
    return None


def src_for_feed(feed) -> str | None:
    src = cached_src(getattr(feed, "favicon_name", None))
    if src:
        return src
    return src_for_url(feed.url)


def src_for_url(url: str) -> str | None:
    path = stored_path(url)
    return f"/favicons/{path.name}" if path else None


def stored_path(url: str) -> Path | None:
    keys = [host_key(url), host_key(homepage_url(url))]
    keys.extend(host_key(site) for site in website_candidates(url))
    seen: set[str] = set()
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        matches = sorted(FAVICON_DIR.glob(f"{key}.*"), key=lambda item: item.stat().st_mtime, reverse=True)
        for match in matches:
            if _file_is_usable(match):
                return match
    return None


def ensure_favicon(url: str, *extra_urls: str) -> Path | None:
    aliases = [item for item in (url, *extra_urls) if item]
    existing = stored_path(url)
    if existing:
        _alias_file(existing, aliases)
        _clear_attempt(url)
        return existing
    fetched = _fetch_icon(url)
    if fetched is None:
        _mark_attempt(url)
        return None
    _alias_file(fetched, aliases)
    _clear_attempt(url)
    return fetched


def capture_for_feed(feed) -> str | None:
    if cached_src(getattr(feed, "favicon_name", None)):
        return feed.favicon_name
    path = ensure_favicon(feed.url)
    if path is None:
        return None
    feed.favicon_name = path.name
    return path.name


def capture_url_async(url: str) -> None:
    def _worker() -> None:
        try:
            ensure_favicon(url)
        except Exception:  # noqa: BLE001
            logger.debug("favicon capture failed for %s", url, exc_info=True)

    Thread(target=_worker, daemon=True, name="newscast-favicon").start()


def capture_for_feed_async(feed_id: int) -> None:
    def _worker() -> None:
        from app.db import SessionLocal
        from app.models import Feed

        db = SessionLocal()
        try:
            feed = db.get(Feed, feed_id)
            if feed is None:
                return
            if capture_for_feed(feed):
                db.commit()
        except Exception:  # noqa: BLE001
            logger.debug("favicon capture failed for feed %s", feed_id, exc_info=True)
        finally:
            db.close()

    Thread(target=_worker, daemon=True, name="newscast-favicon").start()


def backfill_missing_feeds(db) -> dict[str, int]:
    """Reconcile favicon_name from disk; network only for enabled feeds that are truly missing."""
    from app.models import Feed, Story

    linked = 0
    fetched = 0
    skipped = 0
    for feed in db.query(Feed).filter(Feed.enabled.is_(True)).all():
        if cached_src(feed.favicon_name):
            skipped += 1
            continue
        on_disk = stored_path(feed.url)
        if on_disk is not None:
            feed.favicon_name = on_disk.name
            db.commit()
            linked += 1
            continue
        if _attempt_recent(feed.url):
            skipped += 1
            continue
        try:
            if capture_for_feed(feed):
                db.commit()
                fetched += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            _mark_attempt(feed.url)
            skipped += 1
            logger.debug("favicon backfill failed for %s", feed.url, exc_info=True)

    for story in db.query(Story).filter(Story.saved.is_(True)).all():
        url = story.canonical_url or ""
        if not url:
            continue
        if src_for_url(url):
            skipped += 1
            continue
        if _attempt_recent(url):
            skipped += 1
            continue
        try:
            if ensure_favicon(url):
                fetched += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001
            _mark_attempt(url)
            skipped += 1
            logger.debug("favicon backfill failed for saved %s", url, exc_info=True)

    logger.info("favicon backfill: %s linked from disk, %s fetched, %s skipped", linked, fetched, skipped)
    return {"linked": linked, "fetched": fetched, "skipped": skipped}


def capture_missing_feeds() -> None:
    def _worker() -> None:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            backfill_missing_feeds(db)
        finally:
            db.close()

    Thread(target=_worker, daemon=True, name="newscast-favicon-backfill").start()


def _attempts_file() -> Path:
    return FAVICON_DIR / ".attempts.json"


def _load_attempts() -> dict:
    path = _attempts_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_attempts(data: dict) -> None:
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _attempts_file().write_text(json.dumps(data, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        logger.debug("could not write favicon attempts", exc_info=True)


def _attempt_key(url: str) -> str:
    return host_key(homepage_url(url) or url) or host_key(url)


def _attempt_recent(url: str) -> bool:
    key = _attempt_key(url)
    if not key:
        return False
    raw = _load_attempts().get(key)
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when < ATTEMPT_COOLDOWN


def _mark_attempt(url: str) -> None:
    key = _attempt_key(url)
    if not key:
        return
    data = _load_attempts()
    data[key] = datetime.now(timezone.utc).isoformat()
    _save_attempts(data)


def _clear_attempt(url: str) -> None:
    key = _attempt_key(url)
    if not key:
        return
    data = _load_attempts()
    if key not in data:
        return
    data.pop(key, None)
    _save_attempts(data)


def map_for_feeds(feeds) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for feed in feeds:
        src = src_for_feed(feed)
        if not src:
            continue
        keys = [
            feed.name,
            feed.url,
            getattr(feed, "catalog_id", None),
            host_key(feed.url),
            host_key(homepage_url(feed.url)),
        ]
        keys.extend(host_key(site) for site in website_candidates(feed.url))
        for key in keys:
            if key:
                mapping[str(key)] = src
    return mapping


def map_for_stories(stories) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for story in stories:
        url = getattr(story, "canonical_url", "") or ""
        src = src_for_url(url)
        if not src:
            continue
        for key in (
            getattr(story, "source_name", None),
            url,
            host_key(url),
            host_key(homepage_url(url)),
        ):
            if key:
                mapping[str(key)] = src
    return mapping


def lookup(mapping: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if key and key in mapping:
            return mapping[key]
    for key in keys:
        if key:
            src = src_for_url(key)
            if src:
                return src
    return None


def _ext_for(data: bytes, content_type: str, hint: str) -> str | None:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    by_type = {
        "image/png": ".png",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/gif": ".gif",
    }
    if ctype in by_type:
        return by_type[ctype]
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\x00\x00\x01\x00") or data.startswith(b"\x00\x00\x02\x00"):
        return ".ico"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    start = data.lstrip()[:200].lower()
    if start.startswith(b"<svg") or b"<svg" in start:
        return ".svg"
    suffix = Path(hint.split("?", 1)[0]).suffix.lower()
    if suffix in {".png", ".ico", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for item in values:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _looks_like_feed(content_type: str, text: str) -> bool:
    start = (text or "").lstrip()[:400].lower()
    if any(token in start for token in ("<rss", "<feed", "<rdf")):
        return True
    ctype = (content_type or "").lower()
    return any(token in ctype for token in ("rss", "atom+xml")) and "html" not in ctype


def _is_stub_icon(data: bytes) -> bool:
    # Default /favicon.ico is often a 16x16 1-bit placeholder that renders blank.
    if not data.startswith(b"\x00\x00\x01\x00") or len(data) < 14:
        return False
    width = data[6]
    bitcount = int.from_bytes(data[12:14], "little")
    return width <= 16 and bitcount <= 1 and len(data) < 400


def _file_is_usable(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return _ext_for(data, "", path.name) is not None and not _is_stub_icon(data)


def _helper_urls(*urls: str) -> list[str]:
    hosts: list[str] = []
    for url in urls:
        if not url:
            continue
        host = host_key(homepage_url(url) or url)
        if host:
            hosts.append(host)
        hosts.extend(host_key(site) for site in website_candidates(url))
    found: list[str] = []
    for host in _unique(hosts):
        found.extend(template.format(host=host) for template in ICON_HELPERS)
    return found


def _looks_like_icon_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(token in path for token in ("favicon", "apple-touch", "apple-icon")):
        return True
    return Path(path.split("?", 1)[0]).suffix in {".ico", ".png", ".svg", ".webp"}


def _hints_from_feed(text: str, feed_url: str) -> tuple[list[str], list[str]]:
    head, *rest = ITEM_SPLIT_RE.split(text or "", maxsplit=1)
    icons: list[str] = []
    sites: list[str] = []
    for match in FEED_IMAGE_RE.finditer(head):
        raw = (match.group(1) or match.group(2) or "").strip()
        if raw and _looks_like_icon_url(raw):
            icons.append(urljoin(feed_url, raw))
    for match in RSS_LINK_RE.finditer(head):
        href = match.group(1).strip()
        if href.startswith("http"):
            sites.append(href)
            break
    if not sites:
        for match in ATOM_LINK_RE.finditer(head):
            tag = match.group(0).lower()
            href = match.group(1).strip()
            if "rel=" in tag and "self" in tag:
                continue
            if href.startswith("http"):
                sites.append(href)
                break
    if not sites and rest:
        for match in RSS_LINK_RE.finditer(rest[0][:2000]):
            href = match.group(1).strip()
            if href.startswith("http"):
                sites.append(href)
                break
    return _unique(icons), _unique(sites)


def _get(url: str, accept: str, client: httpx.Client | None = None) -> httpx.Response | None:
    try:
        own = client is None
        session = client or httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            response = session.get(url, headers={"Accept": accept})
        finally:
            if own:
                session.close()
        if response.status_code >= 400 or not response.content:
            return None
        ctype = (response.headers.get("content-type") or "").lower()
        limit = 2 * 1024 * 1024 if any(token in ctype for token in ("html", "xml", "rss", "atom")) else MAX_BYTES
        if len(response.content) > limit:
            return None
        return response
    except httpx.HTTPError:
        return None


def _icon_candidates(page_url: str, page_html: str) -> list[str]:
    found: list[str] = []
    for tag in ICON_LINK_RE.findall(page_html or ""):
        href = HREF_RE.search(tag)
        if not href:
            continue
        found.append(urljoin(page_url, href.group(1).strip()))
    home = homepage_url(page_url)
    origin = f"{urlparse(home).scheme}://{urlparse(home).netloc}" if home else ""
    extras = [urljoin(origin, "/favicon.ico")] if origin else []
    parsed = urlparse(page_url)
    if parsed.scheme and parsed.netloc:
        extras.append(f"{parsed.scheme}://{parsed.netloc}/favicon.ico")
    for item in extras:
        if item not in found:
            found.append(item)
    return found


def _save(data: bytes, content_type: str, source_url: str, aliases: list[str]) -> Path | None:
    if _is_stub_icon(data):
        return None
    ext = _ext_for(data, content_type, source_url)
    if ext is None:
        return None
    primary = host_key(homepage_url(aliases[0] if aliases else source_url) or source_url)
    if not primary:
        primary = host_key(source_url)
    if not primary:
        return None
    dest = FAVICON_DIR / f"{primary}{ext}"
    dest.write_bytes(data)
    return dest


def _alias_file(path: Path, urls: list[str]) -> None:
    for url in urls:
        key = host_key(url)
        home = host_key(homepage_url(url))
        extras = {key, home, *(host_key(site) for site in website_candidates(url))}
        for alias in extras:
            if not alias:
                continue
            dest = path.with_name(f"{alias}{path.suffix}")
            if dest.resolve() == path.resolve():
                continue
            try:
                if dest.exists() and dest.stat().st_mtime >= path.stat().st_mtime:
                    continue
                shutil.copyfile(path, dest)
            except OSError:
                continue


def _try_icon(candidate: str, aliases: list[str], client: httpx.Client) -> Path | None:
    response = _get(candidate, "image/*,*/*;q=0.8", client)
    if response is None or _is_stub_icon(response.content):
        return None
    return _save(response.content, response.headers.get("content-type", ""), candidate, aliases)


def _fetch_from_page(page_url: str, aliases: list[str], client: httpx.Client) -> Path | None:
    page = _get(page_url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8", client)
    html = ""
    if page is not None:
        ctype = (page.headers.get("content-type") or "").lower()
        html = page.text if ("html" in ctype or page.text) else ""
        page_url = str(page.url) or page_url
    for candidate in _icon_candidates(page_url, html):
        saved = _try_icon(candidate, aliases, client)
        if saved is not None:
            return saved
    return None


def _fetch_icon(url: str) -> Path | None:
    aliases = [url]
    feed_icons: list[str] = []
    websites: list[str] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        feed = _get(
            url,
            "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
            client,
        )
        if feed is not None:
            ctype = feed.headers.get("content-type") or ""
            text = feed.text or ""
            final = str(feed.url) or url
            if _looks_like_feed(ctype, text):
                icons, sites = _hints_from_feed(text, final)
                feed_icons.extend(icons)
                websites.extend(sites)
            elif "html" in ctype.lower() or "<html" in text[:400].lower():
                saved = _fetch_from_page(final, aliases + [final], client)
                if saved is not None:
                    return saved
            parsed = urlparse(final)
            if parsed.scheme and parsed.netloc:
                websites.append(f"{parsed.scheme}://{parsed.netloc}/")
        for candidate in _unique(feed_icons):
            saved = _try_icon(candidate, aliases, client)
            if saved is not None:
                return saved
        websites.extend(website_candidates(url))
        for site in _unique(websites):
            aliases.append(site)
            saved = _fetch_from_page(site, aliases, client)
            if saved is not None:
                return saved
        for candidate in _helper_urls(url, *websites):
            saved = _try_icon(candidate, aliases, client)
            if saved is not None:
                return saved
    return None


