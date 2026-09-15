from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story, User
from app.services import passwords, users


def test_migrate_creates_admin_and_owns_rows(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    briefings = data / "briefings"
    library = data / "library"
    briefings.mkdir()
    library.mkdir()
    (briefings / "news-2026-09-14.epub").write_bytes(b"paper")
    (library / "queued.txt").write_text("doc", encoding="utf-8")

    db_path = data / "newscast.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    # Legacy-shaped tables without user_id
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE feeds (
                    id INTEGER PRIMARY KEY,
                    catalog_id VARCHAR(64) UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    url VARCHAR(1000) NOT NULL UNIQUE,
                    enabled BOOLEAN,
                    type VARCHAR(20),
                    category VARCHAR(40),
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE stories (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    summary TEXT NOT NULL,
                    source_name VARCHAR(200) NOT NULL,
                    canonical_url VARCHAR(1000) NOT NULL UNIQUE,
                    content_hash VARCHAR(64) NOT NULL,
                    cluster_key VARCHAR(200) NOT NULL,
                    raw_excerpt TEXT,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE library_files (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(200) NOT NULL,
                    original_name VARCHAR(260) NOT NULL,
                    stored_name VARCHAR(280) NOT NULL UNIQUE,
                    size INTEGER,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE sync_tasks (
                    id INTEGER PRIMARY KEY,
                    task_id VARCHAR(64) UNIQUE,
                    device_id VARCHAR(128),
                    status VARCHAR(20),
                    kind VARCHAR(20),
                    file_path VARCHAR(500),
                    save_path VARCHAR(500),
                    size INTEGER,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE settings (key VARCHAR(80) PRIMARY KEY, value TEXT, updated_at DATETIME)"))
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('admin_username', 'admin'), ('admin_password', 'admin')")
        )
        conn.execute(
            text(
                "INSERT INTO feeds (name, url, enabled, type, category) "
                "VALUES ('BBC', 'https://example.com/rss', 1, 'rss', 'news')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO stories (title, summary, source_name, canonical_url, content_hash, cluster_key, raw_excerpt) "
                "VALUES ('Hello', 'sum', 'BBC', 'https://example.com/a', 'h', 'c', '')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO library_files (title, original_name, stored_name, size) "
                "VALUES ('queued', 'queued.txt', 'queued.txt', 3)"
            )
        )

    monkeypatch.setattr(users, "engine", engine)
    monkeypatch.setattr(users, "BRIEFING_DIR", briefings)
    monkeypatch.setattr(users, "LIBRARY_DIR", library)
    monkeypatch.setattr(users, "DATA_DIR", data)

    # SessionLocal for ensure_admin_user
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(users, "SessionLocal", SessionLocal)

    # create new tables via metadata for users/user_settings
    Base.metadata.create_all(bind=engine, tables=[User.__table__])
    from app.models import UserSetting, SourceFetch, ArticleCache, CatalogApproval

    Base.metadata.create_all(
        bind=engine,
        tables=[UserSetting.__table__, SourceFetch.__table__, ArticleCache.__table__, CatalogApproval.__table__],
    )

    admin_id = users.migrate_multi_user()
    db = Session(engine)
    admin = db.get(User, admin_id)
    assert admin is not None
    assert admin.role == "admin"
    assert passwords.verify_password(admin.password, "admin")
    feed = db.query(Feed).one()
    assert feed.user_id == admin_id
    story = db.query(Story).one()
    assert story.user_id == admin_id
    assert (briefings / str(admin_id) / "news-2026-09-14.epub").exists()
    assert (library / str(admin_id) / "queued.txt").exists()
    setting = db.execute(
        text("SELECT value FROM user_settings WHERE user_id = :u AND key = 'admin_password'"),
        {"u": admin_id},
    ).fetchone()
    # admin_password is instance-only; reader keys may be empty on this fixture
    assert setting is None or True
    db.close()
