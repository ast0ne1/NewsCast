from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed
from app.services.categories import (
    add_category,
    category_labels,
    delete_category,
    rename_category,
    seed_builtin_categories,
    slugify,
)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_slugify_and_add_category():
    assert slugify("Marketing & PR") == "marketing-pr"
    db = _session()
    row = add_category(db, "Marketing")
    assert row.key == "marketing"
    assert row.builtin is False
    labels = category_labels(db)
    assert labels["marketing"] == "Marketing"
    assert labels["nordic"] == "Nordic"


def test_cannot_delete_builtin():
    db = _session()
    seed_builtin_categories(db)
    try:
        delete_category(db, "nordic")
        raise AssertionError("builtin delete should fail")
    except ValueError as exc:
        assert "Built-in" in str(exc)


def test_delete_reassigns_feeds():
    db = _session()
    add_category(db, "Architecture")
    db.add(
        Feed(
            name="Dezeen",
            url="https://www.dezeen.com/feed/",
            category="architecture",
            enabled=True,
        )
    )
    db.commit()
    delete_category(db, "architecture")
    assert db.query(Feed).one().category == "news"


def test_rename_user_category():
    db = _session()
    add_category(db, "Romania")
    rename_category(db, "romania", "România")
    assert category_labels(db)["romania"] == "România"
