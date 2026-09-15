import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import env
from app.models import CatalogApproval, Feed
from app.services.categories import BUILTIN_LABELS, category_labels
from app.services.favicon import src_for_feed, src_for_url
from app.services.packages import package_catalog_items

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "recommended_feeds.json"
CATEGORY_LABELS = BUILTIN_LABELS

SOURCE_LABELS = {
    "rss": "RSS",
    "webpage": "Scrape",
    "auto": "Auto",
}


def source_kind(item: dict) -> str:
    kind = (item.get("type") or "rss").strip().lower()
    return kind if kind in SOURCE_LABELS else "rss"


def source_label(item: dict) -> str:
    return SOURCE_LABELS[source_kind(item)]


def load_bundled_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def load_catalog() -> list[dict]:
    items = list(load_bundled_catalog())
    seen_ids = {item["id"] for item in items}
    seen_urls = {item["url"] for item in items}
    for item in package_catalog_items():
        if item["id"] in seen_ids or item["url"] in seen_urls:
            continue
        items.append(item)
        seen_ids.add(item["id"])
        seen_urls.add(item["url"])
    return items


def approved_catalog_ids(db: Session) -> set[str]:
    rows = db.query(CatalogApproval).filter(CatalogApproval.approved.is_(True)).all()
    return {row.catalog_id for row in rows}


def is_catalog_approved(db: Session, catalog_id: str) -> bool:
    row = db.get(CatalogApproval, catalog_id)
    return bool(row and row.approved)


def set_catalog_approvals(db: Session, catalog_ids: set[str] | list[str]) -> None:
    wanted = {cid.strip() for cid in catalog_ids if (cid or "").strip()}
    existing = {row.catalog_id: row for row in db.query(CatalogApproval).all()}
    for catalog_id, row in existing.items():
        row.approved = catalog_id in wanted
    for catalog_id in wanted:
        if catalog_id not in existing:
            db.add(CatalogApproval(catalog_id=catalog_id, approved=True))
    db.commit()


def seed_recommended_feeds(db: Session) -> None:
    if not env.seed_recommended_feeds:
        return
    from app.services.users import ensure_admin_user

    admin = ensure_admin_user(db)
    existing = db.query(Feed).filter(Feed.user_id == admin.id).all()
    by_catalog = {feed.catalog_id: feed for feed in existing if feed.catalog_id}
    used_urls = {feed.url for feed in existing}
    changed = False
    for item in load_catalog():
        feed = by_catalog.get(item["id"])
        if feed:
            if feed.url != item["url"] and item["url"] not in used_urls:
                used_urls.discard(feed.url)
                feed.url = item["url"]
                used_urls.add(item["url"])
                changed = True
            wanted_translate = bool(item.get("translate"))
            if bool(getattr(feed, "translate", False)) != wanted_translate:
                feed.translate = wanted_translate
                if wanted_translate and not (getattr(feed, "translate_provider", None) or "").strip():
                    feed.translate_provider = "global"
                changed = True
            wanted_category = item.get("category", "news")
            if feed.category != wanted_category:
                feed.category = wanted_category
                changed = True
            continue
        if item["url"] in used_urls:
            continue
        db.add(
            Feed(
                user_id=admin.id,
                catalog_id=item["id"],
                name=item["name"],
                url=item["url"],
                enabled=bool(item.get("default_enabled")),
                type=item.get("type", "rss"),
                category=item.get("category", "news"),
                translate=bool(item.get("translate")),
                translate_provider="global",
            )
        )
        used_urls.add(item["url"])
        changed = True
    if changed:
        db.commit()


def catalog_with_status(
    db: Session,
    *,
    user_id: int | None = None,
    approved_only: bool = False,
) -> list[dict]:
    query = db.query(Feed)
    if user_id is not None:
        query = query.filter(Feed.user_id == user_id)
    feeds = query.all()
    by_catalog = {feed.catalog_id: feed for feed in feeds if feed.catalog_id}
    by_url = {feed.url: feed for feed in feeds}
    approved = approved_catalog_ids(db)
    items = []
    labels = category_labels(db)
    for item in load_catalog():
        if approved_only and item["id"] not in approved:
            continue
        existing = by_catalog.get(item["id"]) or by_url.get(item["url"])
        added = bool(existing and existing.enabled)
        items.append(
            {
                **item,
                "label": labels.get(item["category"], item["category"].title()),
                "source_kind": source_kind(item),
                "source_label": source_label(item),
                "added": added,
                "feed_id": existing.id if existing else None,
                "favicon": (src_for_feed(existing) if existing else None) or src_for_url(item["url"]),
                "approved": item["id"] in approved,
            }
        )
    return items


def grouped_catalog(
    db: Session,
    *,
    user_id: int | None = None,
    approved_only: bool = False,
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in catalog_with_status(db, user_id=user_id, approved_only=approved_only):
        grouped.setdefault(item["category"], []).append(item)
    return grouped


def find_catalog_item(catalog_id: str) -> dict | None:
    for item in load_catalog():
        if item["id"] == catalog_id:
            return item
    return None
