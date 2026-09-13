from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.auth import require_x3_token
from app.config import BRIEFING_DIR
from app.db import get_db
from app.services.briefing import current_briefing_payload, normalize_briefing_day, render_txt, write_briefing_files

router = APIRouter(prefix="/api/x3", dependencies=[Depends(require_x3_token)])


def _day_payload(db, day: str | None):
    key = normalize_briefing_day(day)
    if key == "all":
        key = "today"
    stem = "news-yesterday" if key == "yesterday" else "news"
    payload = current_briefing_payload(db, day=key)
    return payload, stem


@router.get("/news")
def x3_news(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    payload, _stem = _day_payload(db, day)
    return payload


@router.get("/news.txt", response_class=PlainTextResponse)
def x3_news_txt(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    payload, stem = _day_payload(db, day)
    write_briefing_files(payload, stem=stem)
    return render_txt(payload)


@router.get("/news.epub")
def x3_news_epub(db: Annotated[Session, Depends(get_db)], day: str = "today"):
    payload, stem = _day_payload(db, day)
    files = write_briefing_files(payload, stem=stem)
    filename = "newscast-news-yesterday.epub" if stem == "news-yesterday" else "newscast-news.epub"
    return FileResponse(
        files["epub"],
        media_type="application/epub+zip",
        filename=filename,
    )
