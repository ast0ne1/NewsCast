from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_x3_token
from app.db import get_db
from app.services import opds
from app.services.categories import slugify

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


@router.get("/categories")
def opds_categories(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.categories_feed(db), "navigation")


@router.get("/categories/{category}")
def opds_category(category: str, db: Annotated[Session, Depends(get_db)]):
    key = slugify(category) or category.strip().lower()
    if not key:
        raise HTTPException(status_code=404, detail="Category not found.")
    return _atom(opds.category_feed(db, key), "acquisition")


@router.get("/library")
def opds_library(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.library_feed(db), "acquisition")
