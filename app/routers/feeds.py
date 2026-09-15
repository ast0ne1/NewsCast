from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import Feed
from app.services.catalog import find_catalog_item, grouped_catalog

router = APIRouter(dependencies=[Depends(require_admin)])


class FeedCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=1000)
    category: str = "news"
    type: str = "auto"
    enabled: bool = True


class FeedUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    category: str | None = None
    enabled: bool | None = None
    schedule_mode: str | None = None
    interval_minutes: int | None = None
    summarize: bool | None = None
    translate: bool | None = None


def _feed_dict(feed: Feed) -> dict:
    return {
        "id": feed.id,
        "catalog_id": feed.catalog_id,
        "name": feed.name,
        "url": feed.url,
        "enabled": feed.enabled,
        "type": feed.type,
        "category": feed.category,
        "last_fetched_at": feed.last_fetched_at.isoformat() if feed.last_fetched_at else None,
        "last_error": feed.last_error,
        "schedule_mode": getattr(feed, "schedule_mode", None) or "global",
        "interval_minutes": getattr(feed, "interval_minutes", None),
        "summarize": bool(getattr(feed, "summarize", True)),
        "translate": bool(getattr(feed, "translate", False)),
        "translate_provider": getattr(feed, "translate_provider", None) or "global",
    }


@router.get("/api/feeds")
def list_feeds(db: Annotated[Session, Depends(get_db)]):
    feeds = db.query(Feed).order_by(Feed.name.asc()).all()
    return {"feeds": [_feed_dict(feed) for feed in feeds]}


@router.post("/api/feeds")
def create_feed(payload: FeedCreate, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.auth import effective_user_id, session_from_request
    from app.models import User

    session = session_from_request(request)
    uid = effective_user_id(session)
    is_admin = bool(session and session.role == "admin")
    user_row = db.get(User, uid) if uid else None
    if not is_admin and not (user_row and user_row.can_add_custom_sources):
        raise HTTPException(status_code=403, detail="Custom sources are not enabled for your account.")
    existing = db.query(Feed).filter(Feed.user_id == uid, Feed.url == payload.url.strip()).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="That feed URL is already added.")
    feed = Feed(
        user_id=uid,
        name=payload.name.strip(),
        url=payload.url.strip(),
        category=payload.category,
        type=payload.type,
        enabled=payload.enabled,
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)
    from app.services.favicon import capture_for_feed_async

    capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.patch("/api/feeds/{feed_id}")
def update_feed(feed_id: int, payload: FeedUpdate, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    if payload.name is not None:
        feed.name = payload.name.strip()
    if payload.url is not None:
        feed.url = payload.url.strip()
    if payload.category is not None:
        feed.category = payload.category
    if payload.enabled is not None:
        feed.enabled = payload.enabled
    if payload.schedule_mode is not None:
        feed.schedule_mode = payload.schedule_mode if payload.schedule_mode in {"global", "custom"} else "global"
    if payload.interval_minutes is not None:
        feed.interval_minutes = payload.interval_minutes if payload.interval_minutes > 0 else None
    if payload.summarize is not None:
        feed.summarize = payload.summarize
    if payload.translate is not None:
        feed.translate = payload.translate
    db.commit()
    db.refresh(feed)
    if feed.enabled:
        from app.services.favicon import cached_src, capture_for_feed_async

        if not cached_src(feed.favicon_name):
            capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.delete("/api/feeds/{feed_id}")
def delete_feed(feed_id: int, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.delete(feed)
    db.commit()
    return {"ok": True}


@router.get("/api/feeds/recommended")
def recommended_feeds(db: Annotated[Session, Depends(get_db)]):
    return {"categories": grouped_catalog(db)}


def add_catalog_feed(db: Session, catalog_id: str, user_id: int = 1, *, require_approved: bool = False) -> dict:
    item = find_catalog_item(catalog_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown recommended feed")
    if require_approved:
        from app.services.catalog import is_catalog_approved

        if not is_catalog_approved(db, catalog_id):
            raise HTTPException(status_code=403, detail="That source is not approved for this household.")
    uid = int(user_id or 1)
    feed = (
        db.query(Feed)
        .filter(Feed.user_id == uid)
        .filter((Feed.catalog_id == catalog_id) | (Feed.url == item["url"]))
        .one_or_none()
    )
    if feed is None:
        feed = Feed(
            user_id=uid,
            catalog_id=item["id"],
            name=item["name"],
            url=item["url"],
            enabled=True,
            type=item.get("type", "rss"),
            category=item.get("category", "news"),
            translate=bool(item.get("translate")),
        )
        db.add(feed)
    else:
        feed.enabled = True
        feed.catalog_id = feed.catalog_id or item["id"]
        feed.category = item.get("category", feed.category)
        feed.translate = bool(item.get("translate"))
    db.commit()
    db.refresh(feed)
    from app.services.favicon import capture_for_feed_async

    capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.post("/api/feeds/recommended/{catalog_id}")
def add_recommended(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.auth import effective_user_id, session_from_request

    session = session_from_request(request)
    is_admin = bool(session and session.role == "admin")
    return add_catalog_feed(
        db,
        catalog_id,
        effective_user_id(session),
        require_approved=not is_admin,
    )


@router.delete("/api/feeds/recommended/{catalog_id}")
def remove_recommended(catalog_id: str, db: Annotated[Session, Depends(get_db)]):
    item = find_catalog_item(catalog_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown recommended feed")
    feed = db.query(Feed).filter((Feed.catalog_id == catalog_id) | (Feed.url == item["url"])).one_or_none()
    if feed is None:
        return {"ok": True, "removed": False}
    db.delete(feed)
    db.commit()
    return {"ok": True, "removed": True}
