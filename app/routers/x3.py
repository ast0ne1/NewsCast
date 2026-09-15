from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.auth import require_opds_user_token, require_x3_token
from app.db import get_db
from app.services.briefing import (
    current_briefing_payload,
    frozen_briefing_path,
    normalize_briefing_day,
    parse_category_from_stem,
)
from app.services.categories import category_labels, slugify
from app.services.paper_naming import (
    day_from_briefing_path,
    paper_category_download_name,
    paper_download_name,
)
from app.services.users import ensure_admin_user

router = APIRouter()


def _day_key(day: str | None) -> str:
    key = normalize_briefing_day(day)
    return "today" if key == "all" else key


def _download_name(db: Session, path, key: str, suffix: str, category: str = "") -> str:
    day = day_from_briefing_path(path.stem)
    if day is not None and category:
        labels = category_labels(db)
        label = labels.get(category) or category
        return paper_category_download_name(db, day, label, suffix=suffix)
    if day is not None:
        return paper_download_name(db, day, suffix=suffix)
    return "newscast-news-yesterday.epub" if key == "yesterday" else f"newscast-news.{suffix}"


def _response_filename(preferred: str | None, fallback: str) -> str:
    name = Path(unquote(preferred or "")).name.strip()
    if name.lower().endswith(".epub") and name.lower() not in {".epub", "epub"}:
        return name
    return fallback


def _admin_user_id(db: Session) -> int:
    return ensure_admin_user(db).id


legacy = APIRouter(prefix="/api/x3", dependencies=[Depends(require_x3_token)])


@legacy.get("/news")
def x3_news(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    return current_briefing_payload(db, day=_day_key(day), user_id=_admin_user_id(db))


@legacy.get("/news.txt", response_class=PlainTextResponse)
def x3_news_txt(db: Annotated[Session, Depends(get_db)], day: str = "today", category: str = ""):
    key = _day_key(day)
    slug = slugify(category) if category.strip() else ""
    uid = _admin_user_id(db)
    path = frozen_briefing_path(key, suffix="txt", fallback=key == "today", category=slug or None, user_id=uid)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    return path.read_text(encoding="utf-8")


@legacy.get("/news.epub")
def x3_news_epub(db: Annotated[Session, Depends(get_db)], day: str = "today", category: str = ""):
    key = _day_key(day)
    slug = slugify(category) if category.strip() else ""
    uid = _admin_user_id(db)
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today", category=slug or None, user_id=uid)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    cat = slug or parse_category_from_stem(path.stem) or ""
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_download_name(db, path, key, "epub", category=cat),
    )


@legacy.get("/papers/{day}/category/{category}/{filename:path}")
def x3_named_category_epub(
    day: str,
    category: str,
    filename: str,
    db: Annotated[Session, Depends(get_db)],
):
    key = _day_key(day)
    slug = slugify(category) or category.strip().lower()
    uid = _admin_user_id(db)
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today", category=slug, user_id=uid)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    fallback = _download_name(db, path, key, "epub", category=slug)
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_response_filename(filename, fallback),
    )


@legacy.get("/papers/{day}/{filename:path}")
def x3_named_epub(day: str, filename: str, db: Annotated[Session, Depends(get_db)]):
    key = _day_key(day)
    uid = _admin_user_id(db)
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today", user_id=uid)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    fallback = _download_name(db, path, key, "epub")
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_response_filename(filename, fallback),
    )


user = APIRouter(prefix="/api/x3/u/{username}")


@user.get("/news")
def x3_u_news(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
    day: str = "today",
):
    return current_briefing_payload(db, day=_day_key(day), user_id=user_id)


@user.get("/news.txt", response_class=PlainTextResponse)
def x3_u_news_txt(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
    day: str = "today",
    category: str = "",
):
    key = _day_key(day)
    slug = slugify(category) if category.strip() else ""
    path = frozen_briefing_path(
        key, suffix="txt", fallback=key == "today", category=slug or None, user_id=user_id
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    return path.read_text(encoding="utf-8")


@user.get("/news.epub")
def x3_u_news_epub(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
    day: str = "today",
    category: str = "",
):
    key = _day_key(day)
    slug = slugify(category) if category.strip() else ""
    path = frozen_briefing_path(
        key, suffix="epub", fallback=key == "today", category=slug or None, user_id=user_id
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    cat = slug or parse_category_from_stem(path.stem) or ""
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_download_name(db, path, key, "epub", category=cat),
    )


@user.get("/papers/{day}/category/{category}/{filename:path}")
def x3_u_named_category_epub(
    username: str,
    day: str,
    category: str,
    filename: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    key = _day_key(day)
    slug = slugify(category) or category.strip().lower()
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today", category=slug, user_id=user_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    fallback = _download_name(db, path, key, "epub", category=slug)
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_response_filename(filename, fallback),
    )


@user.get("/papers/{day}/{filename:path}")
def x3_u_named_epub(
    username: str,
    day: str,
    filename: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    key = _day_key(day)
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today", user_id=user_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    fallback = _download_name(db, path, key, "epub")
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_response_filename(filename, fallback),
    )


router.include_router(legacy)
router.include_router(user)
