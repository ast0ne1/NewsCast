from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.services import passwords, users


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_update_user_permissions_and_password():
    db = _session()
    user = users.create_user(db, username="pat", password="pass1", can_use_ntfy=False)
    assert not user.can_add_custom_sources
    assert not user.can_use_ntfy
    assert not user.can_view_status

    users.update_user(
        db,
        user,
        can_add_custom_sources=True,
        can_use_ntfy=True,
        can_view_status=True,
        active=True,
        new_password="pass2",
    )
    db.refresh(user)
    assert user.can_add_custom_sources
    assert user.can_use_ntfy
    assert user.can_view_status
    assert passwords.verify_password(user.password, "pass2")

    users.update_user(
        db,
        user,
        can_add_custom_sources=False,
        can_use_ntfy=False,
        can_view_status=False,
        active=False,
    )
    db.refresh(user)
    assert not user.can_add_custom_sources
    assert not user.can_use_ntfy
    assert not user.can_view_status
    assert not user.active


def test_admin_keeps_ntfy_and_custom_feeds():
    db = _session()
    admin = User(
        username="admin",
        password=passwords.hash_password("admin"),
        role="admin",
        can_add_custom_sources=True,
        can_use_ntfy=True,
        can_view_status=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    users.update_user(
        db,
        admin,
        can_add_custom_sources=False,
        can_use_ntfy=False,
        can_view_status=False,
    )
    db.refresh(admin)
    assert admin.can_add_custom_sources
    assert admin.can_use_ntfy
    assert admin.can_view_status


def test_user_may_view_status():
    assert not users.user_may_view_status(None)
    member = User(
        username="pat",
        password="x",
        role="user",
        can_view_status=False,
    )
    assert not users.user_may_view_status(member)
    member.can_view_status = True
    assert users.user_may_view_status(member)
    admin = User(username="admin", password="x", role="admin", can_view_status=False)
    assert users.user_may_view_status(admin)


def test_delete_user_removes_owned_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "BRIEFING_DIR", tmp_path / "briefings")
    monkeypatch.setattr(users, "LIBRARY_DIR", tmp_path / "library")
    db = _session()
    admin = users.create_user(db, username="admin", password="admin", role="admin")
    member = users.create_user(db, username="pat", password="pass1")
    from app.models import Feed, Story, UserSetting

    db.add(Feed(user_id=member.id, name="BBC", url="https://example.com/rss", enabled=True))
    db.add(
        Story(
            user_id=member.id,
            title="Hello",
            summary="",
            source_name="BBC",
            canonical_url="https://example.com/a",
            content_hash="a",
            cluster_key="a",
        )
    )
    db.add(UserSetting(user_id=member.id, key="ntfy_topic", value="pat-topic"))
    (tmp_path / "briefings" / str(member.id)).mkdir(parents=True)
    (tmp_path / "briefings" / str(member.id) / "news.epub").write_bytes(b"x")
    db.commit()
    member_id = member.id

    name = users.delete_user(db, member, actor_id=admin.id)
    assert name == "pat"
    assert db.query(User).filter(User.username == "pat").one_or_none() is None
    assert db.query(Feed).filter(Feed.user_id == member_id).count() == 0
    assert db.query(Story).filter(Story.user_id == member_id).count() == 0
    assert not (tmp_path / "briefings" / str(member_id)).exists()


def test_delete_user_rejects_admin():
    db = _session()
    admin = users.create_user(db, username="admin", password="admin", role="admin")
    try:
        users.delete_user(db, admin, actor_id=admin.id)
        raised = False
    except ValueError:
        raised = True
    assert raised
