from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Category, Feed

BUILTIN_LABELS = {
    "news": "World News",
    "nordic": "Nordic",
    "australia": "Australia",
    "technology": "Tech",
    "security": "Security",
    "science": "Science",
    "business": "Business",
    "sport": "Sport",
    "culture": "Culture",
}
DEFAULT_CATEGORY = "news"
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(label: str) -> str:
    slug = _SLUG.sub("-", (label or "").strip().lower()).strip("-")
    return slug[:40]


def seed_builtin_categories(db: Session) -> None:
    existing = {row.key for row in db.query(Category).all()}
    changed = False
    for index, (key, label) in enumerate(BUILTIN_LABELS.items()):
        if key in existing:
            row = db.get(Category, key)
            if row and row.builtin and row.label != label:
                row.label = label
                changed = True
            continue
        db.add(Category(key=key, label=label, builtin=True, sort_order=index))
        changed = True
    if changed:
        db.commit()


def category_labels(db: Session | None = None) -> dict[str, str]:
    if db is None:
        return dict(BUILTIN_LABELS)
    seed_builtin_categories(db)
    rows = db.query(Category).order_by(Category.sort_order.asc(), Category.label.asc()).all()
    return {row.key: row.label for row in rows} or dict(BUILTIN_LABELS)


def list_categories(db: Session) -> list[Category]:
    seed_builtin_categories(db)
    return db.query(Category).order_by(Category.sort_order.asc(), Category.label.asc()).all()


def add_category(db: Session, label: str, key: str = "") -> Category:
    name = (label or "").strip()
    if not name:
        raise ValueError("Enter a category name.")
    slug = slugify(key or name)
    if not slug:
        raise ValueError("Category needs a short id, for example marketing.")
    if db.get(Category, slug):
        raise ValueError("That category already exists.")
    last = db.query(Category).order_by(Category.sort_order.desc()).first()
    order = (last.sort_order + 1) if last else 100
    row = Category(key=slug, label=name[:80], builtin=False, sort_order=max(order, 100))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def rename_category(db: Session, key: str, label: str) -> Category:
    row = db.get(Category, key)
    if row is None:
        raise ValueError("Category not found.")
    name = (label or "").strip()
    if not name:
        raise ValueError("Enter a category name.")
    row.label = name[:80]
    db.commit()
    db.refresh(row)
    return row


def delete_category(db: Session, key: str) -> None:
    row = db.get(Category, key)
    if row is None:
        raise ValueError("Category not found.")
    if row.builtin:
        raise ValueError("Built-in categories cannot be removed.")
    used = db.query(Feed).filter(Feed.category == key).count()
    if used:
        for feed in db.query(Feed).filter(Feed.category == key).all():
            feed.category = DEFAULT_CATEGORY
    db.delete(row)
    db.commit()


def ensure_category(db: Session, key: str, label: str) -> Category:
    slug = slugify(key) or slugify(label)
    if not slug:
        raise ValueError("Package category needs a key.")
    row = db.get(Category, slug)
    if row:
        return row
    return add_category(db, label or slug, slug)
