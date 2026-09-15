from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_opds_user_token, require_x3_token
from app.db import get_db
from app.services import opds
from app.services.categories import slugify
from app.services.users import ensure_admin_user

router = APIRouter()


def _atom(body: str, kind: str) -> Response:
    return Response(
        content=body.encode("utf-8"),
        media_type=f"application/atom+xml;charset=utf-8;profile=opds-catalog;kind={kind}",
    )


def _admin_user_id(db: Session) -> int:
    return ensure_admin_user(db).id


legacy = APIRouter(prefix="/opds", dependencies=[Depends(require_x3_token)])


@legacy.get("", include_in_schema=False)
@legacy.get("/")
def opds_root(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.navigation_feed(db, user_id=_admin_user_id(db)), "navigation")


@legacy.get("/briefing")
def opds_briefing(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.briefing_feed(db, user_id=_admin_user_id(db)), "acquisition")


@legacy.get("/categories")
def opds_categories(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.categories_feed(db, user_id=_admin_user_id(db)), "navigation")


@legacy.get("/categories/{category}")
def opds_category(category: str, db: Annotated[Session, Depends(get_db)]):
    key = slugify(category) or category.strip().lower()
    if not key:
        raise HTTPException(status_code=404, detail="Category not found.")
    return _atom(opds.category_feed(db, key, user_id=_admin_user_id(db)), "acquisition")


@legacy.get("/library")
def opds_library(db: Annotated[Session, Depends(get_db)]):
    return _atom(opds.library_feed(db, user_id=_admin_user_id(db)), "acquisition")


user = APIRouter(prefix="/opds/u/{username}")


@user.get("", include_in_schema=False)
@user.get("/")
def opds_u_root(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    return _atom(opds.navigation_feed(db, user_id=user_id, username=username), "navigation")


@user.get("/briefing")
def opds_u_briefing(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    return _atom(opds.briefing_feed(db, user_id=user_id, username=username), "acquisition")


@user.get("/categories")
def opds_u_categories(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    return _atom(opds.categories_feed(db, user_id=user_id, username=username), "navigation")


@user.get("/categories/{category}")
def opds_u_category(
    username: str,
    category: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    key = slugify(category) or category.strip().lower()
    if not key:
        raise HTTPException(status_code=404, detail="Category not found.")
    return _atom(opds.category_feed(db, key, user_id=user_id, username=username), "acquisition")


@user.get("/library")
def opds_u_library(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[int, Depends(require_opds_user_token)],
):
    return _atom(opds.library_feed(db, user_id=user_id, username=username), "acquisition")


router.include_router(legacy)
router.include_router(user)
