from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_x3_token
from app.db import get_db
from app.services import opds

router = APIRouter(prefix="/opds", dependencies=[Depends(require_x3_token)])


def _atom(body: str, kind: str) -> Response:
    return Response(
        content=body.encode("utf-8"),
        media_type=f"application/atom+xml;charset=utf-8;profile=opds-catalog;kind={kind}",
    )


@router.get("", include_in_schema=False)
@router.get("/")
def opds_root(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.navigation_feed(db), "navigation")


@router.get("/briefing")
def opds_briefing(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.briefing_feed(db), "acquisition")


@router.get("/library")
def opds_library(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.library_feed(db), "acquisition")
