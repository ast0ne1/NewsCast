from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.services.briefing import current_briefing_payload
from app.services.ingest import snapshot, start_ingest

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/stories")
def list_stories(db: Annotated[Session, Depends(get_db)]):
    return current_briefing_payload(db)


@router.get("/api/ingest/status")
def ingest_status():
    return snapshot()


@router.post("/api/ingest")
def ingest_now(feed_id: int | None = None):
    return start_ingest(force=True, feed_id=feed_id)
