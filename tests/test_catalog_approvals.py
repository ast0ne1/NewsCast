from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.routers.feeds import add_catalog_feed
from app.services import catalog, passwords
from app.services.catalog import catalog_with_status, set_catalog_approvals
from fastapi import HTTPException


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_non_admin_catalog_only_shows_approved():
    db = _session()
    set_catalog_approvals(db, {"techcrunch"})
    items = catalog_with_status(db, approved_only=True)
    ids = {item["id"] for item in items}
    assert ids == {"techcrunch"}


def test_custom_source_gated_by_flag():
    db = _session()
    locked = User(
        username="locked",
        password=passwords.hash_password("llll"),
        role="user",
        can_add_custom_sources=False,
    )
    open_user = User(
        username="open",
        password=passwords.hash_password("oooo"),
        role="user",
        can_add_custom_sources=True,
    )
    db.add_all([locked, open_user])
    db.commit()
    assert not locked.can_add_custom_sources
    assert open_user.can_add_custom_sources


def test_add_catalog_requires_approval_for_non_admin():
    db = _session()
    set_catalog_approvals(db, set())
    try:
        add_catalog_feed(db, "techcrunch", user_id=2, require_approved=True)
        raised = False
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 403
    assert raised
    set_catalog_approvals(db, {"techcrunch"})
    result = add_catalog_feed(db, "techcrunch", user_id=2, require_approved=True)
    assert result["catalog_id"] == "techcrunch"
