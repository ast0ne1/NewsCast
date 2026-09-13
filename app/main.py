import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import SessionLocal, init_db
from app.routers import feeds, opds, stories, ui, x3, xteink
from app.config import BACKUPS_DIR, BRIEFING_DIR, DATA_DIR, FAVICON_DIR, LIBRARY_DIR, PACKAGES_DIR, ROOT_DIR, UPDATES_DIR
from app.services.catalog import seed_recommended_feeds
from app.services.categories import seed_builtin_categories
from app.services.favicon import capture_missing_feeds, seed_bundled_favicons
from app.services import briefing, reader_push, settings
from app.services.ingest import run_ingest

SCHEDULER_TICK_MINUTES = 1

logging.basicConfig(level=logging.INFO)
scheduler = BackgroundScheduler()


def _scheduled_ingest() -> None:
    db = SessionLocal()
    try:
        run_ingest(db, force=False)
        briefing.maybe_publish_daily_briefing(db)
        if settings.reader_push_enabled(db):
            reader_push.flush_pending(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    seed_bundled_favicons()
    init_db()
    db = SessionLocal()
    try:
        seed_builtin_categories(db)
        seed_recommended_feeds(db)
    finally:
        db.close()
    capture_missing_feeds()
    scheduler.add_job(
        _scheduled_ingest,
        "interval",
        minutes=SCHEDULER_TICK_MINUTES,
        id="ingest",
        replace_existing=True,
    )
    scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="NewsCast", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")
app.mount("/favicons", StaticFiles(directory=str(FAVICON_DIR)), name="favicons")
app.include_router(ui.public)
app.include_router(ui.router)
app.include_router(feeds.router)
app.include_router(stories.router)
app.include_router(x3.router)
app.include_router(opds.router)
app.include_router(xteink.router)
