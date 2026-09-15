from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATA_DIR, env
from app.models import Base

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    env.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_schema()


def _ensure_schema() -> None:
    with engine.begin() as conn:
        feed_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(feeds)")).fetchall()}
        if "schedule_mode" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN schedule_mode VARCHAR(20) DEFAULT 'global'"))
            conn.execute(text("UPDATE feeds SET schedule_mode = 'global' WHERE schedule_mode IS NULL"))
        if "interval_minutes" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN interval_minutes INTEGER"))
        if "summarize" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN summarize BOOLEAN DEFAULT 1"))
            conn.execute(text("UPDATE feeds SET summarize = 1 WHERE summarize IS NULL"))
        if "favicon_name" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN favicon_name VARCHAR(200)"))
        if "translate" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN translate BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE feeds SET translate = 0 WHERE translate IS NULL"))
        if "translate_provider" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN translate_provider VARCHAR(20) DEFAULT 'global'"))
            conn.execute(
                text(
                    "UPDATE feeds SET translate_provider = 'global' "
                    "WHERE translate_provider IS NULL OR translate_provider = ''"
                )
            )
        if "muted_until" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN muted_until DATETIME"))
        if "keyword_include" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN keyword_include TEXT DEFAULT ''"))
            conn.execute(text("UPDATE feeds SET keyword_include = '' WHERE keyword_include IS NULL"))
        if "keyword_exclude" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN keyword_exclude TEXT DEFAULT ''"))
            conn.execute(text("UPDATE feeds SET keyword_exclude = '' WHERE keyword_exclude IS NULL"))
        if "last_status_code" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN last_status_code INTEGER"))
        if "last_item_count" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN last_item_count INTEGER"))
        if "empty_since" not in feed_cols:
            conn.execute(text("ALTER TABLE feeds ADD COLUMN empty_since DATETIME"))
        story_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stories)")).fetchall()}
        if "favourited" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN favourited BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE stories SET favourited = 0 WHERE favourited IS NULL"))
        if "saved" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN saved BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE stories SET saved = 0 WHERE saved IS NULL"))
        if "expires_at" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN expires_at DATETIME"))
        if "saved_origin" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN saved_origin VARCHAR(20)"))
        if "importance" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN importance INTEGER"))
        if "content_lang" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN content_lang VARCHAR(8)"))
        task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(sync_tasks)")).fetchall()}
        if "kind" not in task_cols:
            conn.execute(text("ALTER TABLE sync_tasks ADD COLUMN kind VARCHAR(20) DEFAULT 'x3'"))
            conn.execute(text("UPDATE sync_tasks SET kind = 'x3' WHERE kind IS NULL OR kind = ''"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
