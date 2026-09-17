from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.services import reader_config, users


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_namespaced_upload_path_reroots_default_and_admin_folder():
    assert reader_config.namespaced_upload_path("/News", "pat", "xteink") == "/News/pat"
    assert reader_config.namespaced_upload_path("/News/admin", "pat", "xteink") == "/News/pat"
    assert reader_config.namespaced_upload_path("/News/pat", "pat", "xteink") == "/News/pat"
    assert (
        reader_config.namespaced_upload_path("/mnt/onboard/News", "pat", "kobo")
        == "/mnt/onboard/News/pat"
    )
    assert reader_config.namespaced_upload_path("/Custom/Books", "pat", "xteink") == "/Custom/Books/pat"


def test_save_reader_settings_are_per_user():
    db = _session()
    admin = users.create_user(db, username="admin", password="admin", role="admin")
    member = users.create_user(db, username="pat", password="pass1")

    reader_config.save_reader_settings(
        db,
        admin,
        reader_device_value="xteink",
        reader_host_value="admin-reader.local",
        reader_upload_path_value="/News",
        reader_push_when_online=True,
    )
    reader_config.save_reader_settings(
        db,
        member,
        reader_device_value="xteink",
        reader_host_value="pat-reader.local",
        reader_upload_path_value="/News",
        reader_push_when_online=False,
    )

    assert reader_config.reader_host(db, admin.id) == "admin-reader.local"
    assert reader_config.reader_host(db, member.id) == "pat-reader.local"
    assert reader_config.reader_upload_dir(db, admin.id) == "/News/admin"
    assert reader_config.reader_upload_dir(db, member.id) == "/News/pat"
    assert reader_config.reader_push_enabled(db, admin.id)
    assert not reader_config.reader_push_enabled(db, member.id)


def test_copy_admin_reader_settings_namespaces_and_sets_nudge():
    db = _session()
    admin = users.create_user(db, username="admin", password="admin", role="admin")
    reader_config.save_reader_settings(
        db,
        admin,
        reader_device_value="xteink",
        reader_host_value="crosspoint.local",
        reader_upload_path_value="/News",
        reader_push_when_online=True,
    )
    member = users.create_user(db, username="pat", password="pass1")
    reader_config.copy_admin_reader_settings(db, member)

    assert reader_config.reader_host(db, member.id) == "crosspoint.local"
    assert reader_config.reader_upload_dir(db, member.id) == "/News/pat"
    assert reader_config.needs_setup_nudge(db, member.id)
    reader_config.clear_setup_nudge(db, member.id)
    assert not reader_config.needs_setup_nudge(db, member.id)


def test_flush_all_enabled_skips_users_without_push(monkeypatch):
    from app.services import reader_push

    db = _session()
    admin = users.create_user(db, username="admin", password="admin", role="admin")
    member = users.create_user(db, username="pat", password="pass1")
    reader_config.save_reader_settings(
        db,
        admin,
        reader_device_value="xteink",
        reader_host_value="admin.local",
        reader_upload_path_value="/News",
        reader_push_when_online=True,
    )
    reader_config.save_reader_settings(
        db,
        member,
        reader_device_value="xteink",
        reader_host_value="pat.local",
        reader_upload_path_value="/News",
        reader_push_when_online=False,
    )
    seen: list[int] = []

    def fake_flush(db_session, user_id=None):
        seen.append(user_id)
        return {"ok": True, "user_id": user_id}

    monkeypatch.setattr(reader_push, "flush_pending", fake_flush)
    results = reader_push.flush_all_enabled(db)
    assert seen == [admin.id]
    assert len(results) == 1
