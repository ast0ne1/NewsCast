from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.auth import require_x3_token
from app.db import get_db
from app.services.briefing import current_briefing_payload, frozen_briefing_path, normalize_briefing_day
from app.services.paper_naming import day_from_briefing_path, paper_download_name

router = APIRouter(prefix="/api/x3", dependencies=[Depends(require_x3_token)])


def _day_key(day: str | None) -> str:
    key = normalize_briefing_day(day)
    return "today" if key == "all" else key


def _download_name(db: Session, path, key: str, suffix: str) -> str:
    day = day_from_briefing_path(path.stem)
    if day is not None:
        return paper_download_name(db, day, suffix=suffix)
    return "newscast-news-yesterday.epub" if key == "yesterday" else f"newscast-news.{suffix}"


@router.get("/news")
def x3_news(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    return current_briefing_payload(db, day=_day_key(day))


@router.get("/news.txt", response_class=PlainTextResponse)
def x3_news_txt(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    key = _day_key(day)
    path = frozen_briefing_path(key, suffix="txt", fallback=key == "today")
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    return path.read_text(encoding="utf-8")


@router.get("/news.epub")
def x3_news_epub(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    key = _day_key(day)
    path = frozen_briefing_path(key, suffix="epub", fallback=key == "today")
    if path is None:
        raise HTTPException(status_code=404, detail="Today's paper is not published yet.")
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=_download_name(db, path, key, "epub"),
    )
