from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.auth import require_x3_token
from app.config import BRIEFING_DIR
from app.db import get_db
from app.services.briefing import current_briefing_payload, render_txt, write_briefing_files

router = APIRouter(prefix="/api/x3", dependencies=[Depends(require_x3_token)])


@router.get("/news")
def x3_news(db: Annotated[Session, Depends(get_db)]):
    return current_briefing_payload(db)


@router.get("/news.txt", response_class=PlainTextResponse)
def x3_news_txt(db: Annotated[Session, Depends(get_db)]):
    payload = current_briefing_payload(db)
    write_briefing_files(payload)
    return render_txt(payload)


@router.get("/news.epub")
def x3_news_epub(db: Annotated[Session, Depends(get_db)]):
    payload = current_briefing_payload(db)
    files = write_briefing_files(payload)
    return FileResponse(
        files["epub"],
        media_type="application/epub+zip",
        filename="newscast-news.epub",
    )
