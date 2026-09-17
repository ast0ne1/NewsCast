from __future__ import annotations

import logging
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import BRIEFING_DIR, DATA_DIR, LIBRARY_DIR
from app.db import SessionLocal, engine
from app.models import User
from app.services import passwords, settings

logger = logging.getLogger("newscast.users")

LOGIN_TOKEN_TTL = timedelta(days=7)

USER_SETTING_KEYS = (
    "reader_device",
    "reader_host",
    "reader_upload_path",
    "reader_push_when_online",
    "reader_ssh_port",
    "reader_ssh_user",
    "reader_ssh_password",
    "reader_title_pattern",
    "reader_category_title_pattern",
    "reader_date_format",
    "reader_paper_label",
    "translate_target_lang",
    "briefing_limit",
    "briefing_min_importance",
    "briefing_category_mix",
    "briefing_category_shares",
    "briefing_category_opds_keys",
    "briefing_publish_at",
    "keyword_include",
    "keyword_exclude",
    "ntfy_enabled",
    "ntfy_server",
    "ntfy_topic",
    "ntfy_token",
    "ntfy_notify_on_publish",
    "ntfy_notify_on_push",
    "ntfy_last_publish_notified_day",
    "ntfy_last_push_notified_day",
    "x3_sync_token",
    "ui_lang",
)

OWNED_TABLES = ("feeds", "stories", "library_files", "sync_tasks")


def ensure_admin_user(db: Session | None = None) -> User:
    own = db is None
    session = db or SessionLocal()
    try:
        existing = session.query(User).order_by(User.id.asc()).first()
        if existing is not None:
            return existing
        username, password = settings.get_admin_credentials(session)
        username = (username or settings.DEFAULT_ADMIN_USERNAME).strip() or settings.DEFAULT_ADMIN_USERNAME
        raw = password or settings.DEFAULT_ADMIN_PASSWORD
        hashed = raw if passwords.is_hashed(raw) else passwords.hash_password(raw)
        if not passwords.is_hashed(raw):
            settings.set_value(session, "admin_password", hashed)
        user = User(
            username=username,
            password=hashed,
            role="admin",
            can_add_custom_sources=True,
            can_use_ntfy=True,
            can_view_status=True,
            active=True,
            ui_lang=(settings.get_value(session, "ui_lang") or None),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info("Created household admin user id=%s username=%s", user.id, user.username)
        return user
    finally:
        if own:
            session.close()


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = "user",
    can_add_custom_sources: bool = False,
    can_use_ntfy: bool = False,
    can_view_status: bool = False,
) -> User:
    name = (username or "").strip()
    if not name:
        raise ValueError("Username is required.")
    if db.query(User).filter(User.username == name).one_or_none():
        raise ValueError("That username is already taken.")
    if len(password or "") < 4:
        raise ValueError("Password must be at least 4 characters.")
    is_admin = role == "admin"
    user = User(
        username=name,
        password=passwords.hash_password(password),
        role=role if role in {"admin", "user"} else "user",
        can_add_custom_sources=True if is_admin else bool(can_add_custom_sources),
        can_use_ntfy=True if is_admin else bool(can_use_ntfy),
        can_view_status=True if is_admin else bool(can_view_status),
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    user: User,
    *,
    can_add_custom_sources: bool | None = None,
    can_use_ntfy: bool | None = None,
    can_view_status: bool | None = None,
    active: bool | None = None,
    new_password: str | None = None,
) -> User:
    """Update permissions, active flag, and optional password. Admins keep full access."""
    if user.role == "admin":
        user.can_add_custom_sources = True
        user.can_use_ntfy = True
        user.can_view_status = True
        if active is False:
            other_admins = (
                db.query(User)
                .filter(User.role == "admin", User.active.is_(True), User.id != user.id)
                .count()
            )
            if other_admins == 0:
                raise ValueError("Keep at least one active admin account.")
    else:
        if can_add_custom_sources is not None:
            user.can_add_custom_sources = bool(can_add_custom_sources)
        if can_use_ntfy is not None:
            user.can_use_ntfy = bool(can_use_ntfy)
        if can_view_status is not None:
            user.can_view_status = bool(can_view_status)
        if active is not None:
            user.active = bool(active)

    if new_password is not None and str(new_password).strip():
        raw = str(new_password).strip()
        if len(raw) < 4:
            raise ValueError("Password must be at least 4 characters.")
        user.password = passwords.hash_password(raw)

    db.commit()
    db.refresh(user)
    return user


def update_user_permissions(
    db: Session,
    user: User,
    *,
    can_add_custom_sources: bool | None = None,
    can_use_ntfy: bool | None = None,
    can_view_status: bool | None = None,
) -> User:
    return update_user(
        db,
        user,
        can_add_custom_sources=can_add_custom_sources,
        can_use_ntfy=can_use_ntfy,
        can_view_status=can_view_status,
    )


def delete_user(db: Session, user: User, *, actor_id: int | None = None) -> str:
    """Remove a household user and their owned data. Returns the username removed."""
    from app.models import Category, Feed, LibraryFile, Story, SyncTask, UserSetting

    if user.role == "admin":
        raise ValueError("Admin accounts cannot be removed from People. Use General to change the admin login.")
    if actor_id is not None and int(user.id) == int(actor_id):
        raise ValueError("You cannot remove the account you are signed in as.")

    uid = int(user.id)
    username = user.username

    db.query(UserSetting).filter(UserSetting.user_id == uid).delete(synchronize_session=False)
    db.query(Feed).filter(Feed.user_id == uid).delete(synchronize_session=False)
    db.query(Story).filter(Story.user_id == uid).delete(synchronize_session=False)
    db.query(LibraryFile).filter(LibraryFile.user_id == uid).delete(synchronize_session=False)
    db.query(SyncTask).filter(SyncTask.user_id == uid).delete(synchronize_session=False)
    db.query(Category).filter(Category.user_id == uid).delete(synchronize_session=False)
    db.delete(user)
    db.commit()

    for root in (BRIEFING_DIR / str(uid), LIBRARY_DIR / str(uid)):
        if root.exists():
            try:
                shutil.rmtree(root)
            except OSError:
                logger.warning("Could not remove user files under %s", root)

    return username


def user_may_use_ntfy(user: User | None) -> bool:
    if user is None:
        return False
    if user.role == "admin":
        return True
    return bool(user.can_use_ntfy)


def user_may_view_status(user: User | None) -> bool:
    if user is None:
        return False
    if user.role == "admin":
        return True
    return bool(user.can_view_status)


def issue_login_token(db: Session, user: User, *, ttl: timedelta | None = None) -> str:
    token = secrets.token_urlsafe(32)
    user.login_token = token
    user.login_token_expires = datetime.now(timezone.utc) + (ttl or LOGIN_TOKEN_TTL)
    db.commit()
    db.refresh(user)
    return token


def consume_login_token(db: Session, token: str) -> User | None:
    raw = (token or "").strip()
    if not raw:
        return None
    user = db.query(User).filter(User.login_token == raw).one_or_none()
    if user is None or not user.active:
        return None
    expires = user.login_token_expires
    if expires is None:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        user.login_token = None
        user.login_token_expires = None
        db.commit()
        return None
    user.login_token = None
    user.login_token_expires = None
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.role.asc(), User.username.asc()).all()


def _table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _index_names(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA index_list({table})")).fetchall()}


def _unique_indexes_on_column(conn, table: str, column: str) -> list[str]:
    found: list[str] = []
    for row in conn.execute(text(f"PRAGMA index_list({table})")).fetchall():
        name = row[1]
        unique = bool(row[2])
        if not unique:
            continue
        cols = [info[2] for info in conn.execute(text(f"PRAGMA index_info('{name}')")).fetchall()]
        if cols == [column]:
            found.append(name)
    return found


def _add_user_id_column(conn, table: str, *, nullable: bool = False) -> bool:
    cols = _table_columns(conn, table)
    if "user_id" in cols:
        return False
    # SQLite ADD COLUMN cannot easily add NOT NULL without default; use DEFAULT then backfill.
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 1"))
    if not nullable:
        conn.execute(text(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL"))
    return True


def _copy_admin_user_settings(conn, admin_id: int) -> None:
    for key in USER_SETTING_KEYS:
        row = conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).fetchone()
        if row is None or row[0] is None or row[0] == "":
            continue
        existing = conn.execute(
            text("SELECT 1 FROM user_settings WHERE user_id = :uid AND key = :k"),
            {"uid": admin_id, "k": key},
        ).fetchone()
        if existing:
            continue
        conn.execute(
            text(
                "INSERT INTO user_settings (user_id, key, value, updated_at) "
                "VALUES (:uid, :k, :v, CURRENT_TIMESTAMP)"
            ),
            {"uid": admin_id, "k": key, "v": row[0]},
        )


def _move_tree_into_user_subdir(root: Path, admin_id: int) -> None:
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return
    user_dir = root / str(admin_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    for child in list(root.iterdir()):
        if child.name.isdigit() and child.is_dir():
            continue
        if child == user_dir:
            continue
        dest = user_dir / child.name
        if dest.exists():
            continue
        shutil.move(str(child), str(dest))


def _rebuild_feeds_table(conn) -> None:
    """Drop global unique on url/catalog_id; enforce per-user uniqueness."""
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE feeds_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                catalog_id VARCHAR(64),
                name VARCHAR(200) NOT NULL,
                url VARCHAR(1000) NOT NULL,
                enabled BOOLEAN,
                type VARCHAR(20),
                category VARCHAR(40),
                schedule_mode VARCHAR(20),
                interval_minutes INTEGER,
                summarize BOOLEAN,
                translate BOOLEAN,
                translate_provider VARCHAR(20),
                created_at DATETIME,
                last_fetched_at DATETIME,
                last_error TEXT,
                favicon_name VARCHAR(200),
                muted_until DATETIME,
                keyword_include TEXT,
                keyword_exclude TEXT,
                last_status_code INTEGER,
                last_item_count INTEGER,
                empty_since DATETIME,
                CONSTRAINT uq_feeds_user_url UNIQUE (user_id, url),
                CONSTRAINT uq_feeds_user_catalog UNIQUE (user_id, catalog_id)
            )
            """
        )
    )
    cols = _table_columns(conn, "feeds")
    select_cols = [
        "id",
        "COALESCE(user_id, 1)" if "user_id" in cols else "1",
        "catalog_id" if "catalog_id" in cols else "NULL",
        "name",
        "url",
        "enabled" if "enabled" in cols else "1",
        "type" if "type" in cols else "'rss'",
        "category" if "category" in cols else "'news'",
        "schedule_mode" if "schedule_mode" in cols else "'global'",
        "interval_minutes" if "interval_minutes" in cols else "NULL",
        "summarize" if "summarize" in cols else "1",
        "translate" if "translate" in cols else "0",
        "translate_provider" if "translate_provider" in cols else "'global'",
        "created_at" if "created_at" in cols else "CURRENT_TIMESTAMP",
        "last_fetched_at" if "last_fetched_at" in cols else "NULL",
        "last_error" if "last_error" in cols else "NULL",
        "favicon_name" if "favicon_name" in cols else "NULL",
        "muted_until" if "muted_until" in cols else "NULL",
        "keyword_include" if "keyword_include" in cols else "''",
        "keyword_exclude" if "keyword_exclude" in cols else "''",
        "last_status_code" if "last_status_code" in cols else "NULL",
        "last_item_count" if "last_item_count" in cols else "NULL",
        "empty_since" if "empty_since" in cols else "NULL",
    ]
    conn.execute(text(f"INSERT INTO feeds_new SELECT {', '.join(select_cols)} FROM feeds"))
    conn.execute(text("DROP TABLE feeds"))
    conn.execute(text("ALTER TABLE feeds_new RENAME TO feeds"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feeds_user_id ON feeds (user_id)"))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def _rebuild_stories_table(conn) -> None:
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE stories_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title VARCHAR(500) NOT NULL,
                summary TEXT NOT NULL,
                source_name VARCHAR(200) NOT NULL,
                canonical_url VARCHAR(1000) NOT NULL,
                published_at DATETIME,
                content_hash VARCHAR(64) NOT NULL,
                cluster_key VARCHAR(200) NOT NULL,
                raw_excerpt TEXT,
                favourited BOOLEAN,
                saved BOOLEAN,
                saved_origin VARCHAR(20),
                expires_at DATETIME,
                importance INTEGER,
                content_lang VARCHAR(8),
                created_at DATETIME,
                CONSTRAINT uq_stories_user_url UNIQUE (user_id, canonical_url)
            )
            """
        )
    )
    cols = _table_columns(conn, "stories")
    select_cols = [
        "id",
        "COALESCE(user_id, 1)" if "user_id" in cols else "1",
        "title",
        "summary",
        "source_name",
        "canonical_url",
        "published_at" if "published_at" in cols else "NULL",
        "content_hash",
        "cluster_key",
        "raw_excerpt" if "raw_excerpt" in cols else "''",
        "favourited" if "favourited" in cols else "0",
        "saved" if "saved" in cols else "0",
        "saved_origin" if "saved_origin" in cols else "NULL",
        "expires_at" if "expires_at" in cols else "NULL",
        "importance" if "importance" in cols else "NULL",
        "content_lang" if "content_lang" in cols else "NULL",
        "created_at" if "created_at" in cols else "CURRENT_TIMESTAMP",
    ]
    conn.execute(text(f"INSERT INTO stories_new SELECT {', '.join(select_cols)} FROM stories"))
    conn.execute(text("DROP TABLE stories"))
    conn.execute(text("ALTER TABLE stories_new RENAME TO stories"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stories_user_id ON stories (user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stories_content_hash ON stories (content_hash)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stories_cluster_key ON stories (cluster_key)"))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def _rebuild_library_table(conn) -> None:
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE library_files_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(260) NOT NULL,
                stored_name VARCHAR(280) NOT NULL,
                size INTEGER,
                created_at DATETIME,
                CONSTRAINT uq_library_user_stored UNIQUE (user_id, stored_name)
            )
            """
        )
    )
    cols = _table_columns(conn, "library_files")
    uid_expr = "COALESCE(user_id, 1)" if "user_id" in cols else "1"
    conn.execute(
        text(
            f"INSERT INTO library_files_new "
            f"SELECT id, {uid_expr}, title, original_name, stored_name, size, created_at "
            f"FROM library_files"
        )
    )
    conn.execute(text("DROP TABLE library_files"))
    conn.execute(text("ALTER TABLE library_files_new RENAME TO library_files"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_library_files_user_id ON library_files (user_id)"))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def _needs_feeds_rebuild(conn) -> bool:
    for name in _unique_indexes_on_column(conn, "feeds", "url"):
        return True
    # Also detect UNIQUE constraint without composite via index list covering only url
    indexes = _index_names(conn, "feeds")
    if "uq_feeds_user_url" in indexes:
        return False
    # Fresh create_all already has composite; old DBs have sqlite_autoindex or ix on url alone
    for row in conn.execute(text("PRAGMA index_list(feeds)")).fetchall():
        if not row[2]:
            continue
        cols = [info[2] for info in conn.execute(text(f"PRAGMA index_info('{row[1]}')")).fetchall()]
        if cols == ["url"] or cols == ["catalog_id"]:
            return True
    return False


def _needs_stories_rebuild(conn) -> bool:
    if "uq_stories_user_url" in _index_names(conn, "stories"):
        return False
    for row in conn.execute(text("PRAGMA index_list(stories)")).fetchall():
        if not row[2]:
            continue
        cols = [info[2] for info in conn.execute(text(f"PRAGMA index_info('{row[1]}')")).fetchall()]
        if cols == ["canonical_url"]:
            return True
    return False


def _needs_library_rebuild(conn) -> bool:
    if "uq_library_user_stored" in _index_names(conn, "library_files"):
        return False
    for row in conn.execute(text("PRAGMA index_list(library_files)")).fetchall():
        if not row[2]:
            continue
        cols = [info[2] for info in conn.execute(text(f"PRAGMA index_info('{row[1]}')")).fetchall()]
        if cols == ["stored_name"]:
            return True
    return False


def migrate_multi_user() -> int:
    """Ensure users table, admin row, ownership columns, path moves, and setting copies.

    Returns admin user id.
    """
    # Schema patches must run before any ORM User query (ensure_admin_user).
    with engine.begin() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }
        if "users" in tables:
            user_cols = _table_columns(conn, "users")
            if "can_use_ntfy" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN can_use_ntfy INTEGER DEFAULT 0"))
                conn.execute(text("UPDATE users SET can_use_ntfy = 1 WHERE role = 'admin'"))
            if "can_view_status" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN can_view_status INTEGER DEFAULT 0"))
                conn.execute(text("UPDATE users SET can_view_status = 1 WHERE role = 'admin'"))

    admin = ensure_admin_user()
    admin_id = admin.id
    if admin.role == "admin" and (not bool(admin.can_use_ntfy) or not bool(admin.can_view_status)):
        with SessionLocal() as session:
            row = session.get(User, admin_id)
            if row is not None:
                row.can_use_ntfy = True
                row.can_add_custom_sources = True
                row.can_view_status = True
                session.commit()

    with engine.begin() as conn:
        for table in OWNED_TABLES:
            tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
            if table not in tables:
                continue
            _add_user_id_column(conn, table)
            conn.execute(text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})

        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "categories" in tables:
            cols = _table_columns(conn, "categories")
            if "user_id" not in cols:
                conn.execute(text("ALTER TABLE categories ADD COLUMN user_id INTEGER"))
            # Builtins stay NULL; leave customs NULL until assigned (legacy → shared for v1 admin)

        if "feeds" in tables and _needs_feeds_rebuild(conn):
            logger.info("Rebuilding feeds table for per-user uniqueness")
            _rebuild_feeds_table(conn)
        if "stories" in tables and _needs_stories_rebuild(conn):
            logger.info("Rebuilding stories table for per-user uniqueness")
            _rebuild_stories_table(conn)
        if "library_files" in tables and _needs_library_rebuild(conn):
            logger.info("Rebuilding library_files table for per-user uniqueness")
            _rebuild_library_table(conn)

        _copy_admin_user_settings(conn, admin_id)

    _move_tree_into_user_subdir(BRIEFING_DIR, admin_id)
    _move_tree_into_user_subdir(LIBRARY_DIR, admin_id)

    # Relocate library DB paths that still point at top-level files
    with engine.begin() as conn:
        if "library_files" in {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }:
            rows = conn.execute(text("SELECT id, user_id, stored_name FROM library_files")).fetchall()
            for row_id, user_id, stored in rows:
                name = stored or ""
                if "/" in name or "\\" in name:
                    continue
                nested = f"{user_id or admin_id}/{name}"
                src = LIBRARY_DIR / nested
                if not src.exists():
                    flat = LIBRARY_DIR / name
                    if flat.exists():
                        src.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(flat), str(src))
                conn.execute(
                    text("UPDATE library_files SET stored_name = :s WHERE id = :id"),
                    {"s": nested, "id": row_id},
                )

    cache_dir = DATA_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "feeds").mkdir(parents=True, exist_ok=True)
    (cache_dir / "articles").mkdir(parents=True, exist_ok=True)
    return admin_id
