from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import PACKAGES_DIR
from app.models import Feed
from app.services.categories import ensure_category, slugify

FORMAT = "newscast-package"
FORMAT_VERSION = 1
_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")


def _http_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def package_catalog_id(package_id: str, feed_id: str) -> str:
    return f"{package_id}.{feed_id}"


def validate_package(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Package must be a JSON object.")
    if payload.get("format") != FORMAT:
        raise ValueError("That file is not a NewsCast package.")
    try:
        version = int(payload.get("format_version") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Package format version must be a number.") from exc
    if version != FORMAT_VERSION:
        raise ValueError("This NewsCast build cannot read that package version.")
    package_id = slugify(str(payload.get("id") or payload.get("name") or ""))
    if not package_id or not _ID.match(package_id):
        raise ValueError("Package id must be a short slug, for example romania.")
    name = str(payload.get("name") or package_id).strip()
    category = payload.get("category") or {}
    if not isinstance(category, dict):
        raise ValueError("Package category must be an object with key and label.")
    cat_key = slugify(str(category.get("key") or package_id))
    cat_label = str(category.get("label") or name).strip()
    if not cat_key or not cat_label:
        raise ValueError("Package category needs a key and a label.")
    feeds = payload.get("feeds") or []
    if not isinstance(feeds, list) or not feeds:
        raise ValueError("Package needs at least one feed.")
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    cleaned: list[dict] = []
    for raw in feeds:
        if not isinstance(raw, dict):
            raise ValueError("Each feed must be an object.")
        feed_id = slugify(str(raw.get("id") or raw.get("name") or ""))
        if not feed_id or feed_id in seen_ids:
            raise ValueError("Each feed needs a unique id.")
        url = str(raw.get("url") or "").strip()
        if not _http_url(url) or url in seen_urls:
            raise ValueError(f"Feed {feed_id} needs a unique http(s) URL.")
        kind = str(raw.get("type") or "rss").strip().lower()
        if kind not in {"rss", "webpage", "auto"}:
            kind = "rss"
        seen_ids.add(feed_id)
        seen_urls.add(url)
        cleaned.append(
            {
                "id": feed_id,
                "name": str(raw.get("name") or feed_id).strip()[:200],
                "url": url,
                "type": kind,
                "translate": bool(raw.get("translate")),
                "default_enabled": bool(raw.get("default_enabled")),
                "category": cat_key,
            }
        )
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": package_id,
        "name": name[:80],
        "version": str(payload.get("version") or "1.0.0"),
        "category": {"key": cat_key, "label": cat_label[:80]},
        "feeds": cleaned,
    }


def package_path(package_id: str) -> Path:
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    return PACKAGES_DIR / f"{package_id}.json"


def save_package(package: dict) -> Path:
    cleaned = validate_package(package)
    path = package_path(cleaned["id"])
    path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    return path


def load_package_files() -> list[dict]:
    if not PACKAGES_DIR.exists():
        return []
    packages = []
    for path in sorted(PACKAGES_DIR.glob("*.json")):
        try:
            packages.append(validate_package(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return packages


def package_catalog_items() -> list[dict]:
    items = []
    for package in load_package_files():
        cat = package["category"]
        for feed in package["feeds"]:
            items.append(
                {
                    "id": package_catalog_id(package["id"], feed["id"]),
                    "name": feed["name"],
                    "url": feed["url"],
                    "category": cat["key"],
                    "type": feed["type"],
                    "translate": feed["translate"],
                    "default_enabled": feed["default_enabled"],
                    "package_id": package["id"],
                    "package_name": package["name"],
                }
            )
    return items


def import_package(db: Session, payload) -> dict:
    package = validate_package(payload)
    save_package(package)
    ensure_category(db, package["category"]["key"], package["category"]["label"])
    from app.services.users import ensure_admin_user

    admin = ensure_admin_user(db)
    existing = db.query(Feed).filter(Feed.user_id == admin.id).all()
    used_urls = {feed.url for feed in existing}
    created = 0
    for feed in package["feeds"]:
        catalog_id = package_catalog_id(package["id"], feed["id"])
        if feed["url"] in used_urls:
            continue
        if any(row.catalog_id == catalog_id for row in existing):
            continue
        db.add(
            Feed(
                user_id=admin.id,
                catalog_id=catalog_id,
                name=feed["name"],
                url=feed["url"],
                enabled=bool(feed.get("default_enabled")),
                type=feed.get("type") or "rss",
                category=package["category"]["key"],
                translate=bool(feed.get("translate")),
                translate_provider="global",
            )
        )
        used_urls.add(feed["url"])
        created += 1
    db.commit()
    return {"package": package, "created": created}


def export_category(db: Session, category_key: str, catalog_items: list[dict]) -> dict:
    from app.services.categories import category_labels

    labels = category_labels(db)
    label = labels.get(category_key, category_key)
    slug = slugify(category_key) or "package"
    feeds = []
    seen_urls: set[str] = set()
    for item in catalog_items:
        if item.get("category") != category_key:
            continue
        url = item.get("url") or ""
        if not url or url in seen_urls:
            continue
        raw_id = str(item.get("id") or "")
        feed_id = raw_id.split(".", 1)[-1] if "." in raw_id else raw_id
        feeds.append(
            {
                "id": slugify(feed_id) or slugify(item.get("name") or "feed"),
                "name": item.get("name"),
                "url": url,
                "type": item.get("type") or item.get("source_kind") or "rss",
                "translate": bool(item.get("translate")),
                "default_enabled": False,
            }
        )
        seen_urls.add(url)
    for feed in db.query(Feed).filter(Feed.category == category_key).order_by(Feed.name.asc()).all():
        if feed.url in seen_urls:
            continue
        feeds.append(
            {
                "id": slugify(feed.catalog_id or feed.name) or f"feed-{feed.id}",
                "name": feed.name,
                "url": feed.url,
                "type": feed.type or "rss",
                "translate": bool(feed.translate),
                "default_enabled": False,
            }
        )
        seen_urls.add(feed.url)
    if not feeds:
        raise ValueError("That category has no sources to export.")
    return validate_package(
        {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "id": slug,
            "name": label,
            "version": "1.0.0",
            "category": {"key": slug, "label": label},
            "feeds": feeds,
        }
    )
