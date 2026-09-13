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
        story_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stories)")).fetchall()}
        if "favourited" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN favourited BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE stories SET favourited = 0 WHERE favourited IS NULL"))
        if "saved" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN saved BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE stories SET saved = 0 WHERE saved IS NULL"))
        if "expires_at" not in story_cols:
            conn.execute(text("ALTER TABLE stories ADD COLUMN expires_at DATETIME"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
