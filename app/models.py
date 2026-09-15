from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(20), default="user")
    can_add_custom_sources: Mapped[bool] = mapped_column(Boolean, default=False)
    can_use_ntfy: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    login_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    login_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ui_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)


class UserSetting(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogApproval(Base):
    __tablename__ = "catalog_approvals"

    catalog_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)


class SourceFetch(Base):
    __tablename__ = "source_fetches"

    url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    body_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ArticleCache(Base):
    __tablename__ = "article_cache"

    canonical_url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_feeds_user_url"),
        UniqueConstraint("user_id", "catalog_id", name="uq_feeds_user_catalog"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    catalog_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    type: Mapped[str] = mapped_column(String(20), default="rss")
    category: Mapped[str] = mapped_column(String(40), default="news")
    schedule_mode: Mapped[str] = mapped_column(String(20), default="global")
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summarize: Mapped[bool] = mapped_column(Boolean, default=True)
    translate: Mapped[bool] = mapped_column(Boolean, default=False)
    translate_provider: Mapped[str] = mapped_column(String(20), default="global")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    favicon_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    keyword_include: Mapped[str] = mapped_column(Text, default="")
    keyword_exclude: Mapped[str] = mapped_column(Text, default="")
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    empty_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (UniqueConstraint("user_id", "canonical_url", name="uq_stories_user_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(200))
    canonical_url: Mapped[str] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    cluster_key: Mapped[str] = mapped_column(String(200), index=True)
    raw_excerpt: Mapped[str] = mapped_column(Text, default="")
    favourited: Mapped[bool] = mapped_column(Boolean, default=False)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LibraryFile(Base):
    __tablename__ = "library_files"
    __table_args__ = (UniqueConstraint("user_id", "stored_name", name="uq_library_user_stored"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    title: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(260))
    stored_name: Mapped[str] = mapped_column(String(280))
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncTask(Base):
    __tablename__ = "sync_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    kind: Mapped[str] = mapped_column(String(20), default="x3")
    file_path: Mapped[str] = mapped_column(String(500))
    save_path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
