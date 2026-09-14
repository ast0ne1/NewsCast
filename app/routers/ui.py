import json
import logging
from datetime import date, timedelta
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import __asset_rev__, __author__, __version__
from app.auth import attach_session, clear_session, credentials_match, is_signed_in, require_admin, safe_next
from app.config import ROOT_DIR, env
from app.db import get_db
from app.models import Feed, LibraryFile, Story, SyncTask, utcnow
from app.services import backup, favicon, hostname, library, paper_naming, qrcode, reader_push, settings, update
from app.services.briefing import (
    briefing_path,
    briefing_publish_at,
    current_saved_stories,
    current_stories,
    format_published,
    normalize_briefing_day,
    normalize_publish_at,
    paper_status,
    publish_daily_briefing,
    search_stories,
)
from app.services.health import feed_health, feed_health_label
from app.services.system_stats import status_health
from app.services.delivery import delivery_status
from app.services.schedule import feed_is_muted, normalize_optional_clock
from app.services import saved as saved_articles
from app.services.catalog import catalog_with_status, grouped_catalog
from app.services.categories import category_labels, list_categories
from app.services.ingest import snapshot, start_ingest
from app.services import categories as category_service
from app.services import packages as package_service

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
logger = logging.getLogger("newscast.ui")

SETTINGS_TABS = (
    ("device", "Device"),
    ("schedule", "Schedule"),
    ("filters", "Filters"),
    ("llm", "LLM"),
    ("reader", "Reader"),
    ("categories", "Categories"),
    ("catalog", "Catalog"),
    ("backup", "Backup/Restore"),
    ("update", "Update"),
    ("about", "About"),
)
SETTINGS_TAB_KEYS = {key for key, _label in SETTINGS_TABS}
SETTINGS_TAB_ALIASES = {"access": "device"}
SETTINGS_SAVE_TABS = {"device", "schedule", "filters", "llm", "reader", "update"}
SETTINGS_LEDES = {
    "device": "Colour palette, admin login, hostname, and the Home or Work name for this copy.",
    "schedule": "How often sources refresh, when the reader newspaper publishes, and how many stories Briefing shows.",
    "filters": "Words to keep or drop across every source. A feed can add more on Feeds.",
    "llm": "OpenAI or Ollama for short summaries. Refresh still works without a model.",
    "reader": "Xteink with CrossPoint, or Kobo with KOReader. Catalog login, paper title pattern, and push when the reader is on Wi-Fi.",
    "categories": "Built-in groups stay. Add a country or topic, then fill it from Catalog.",
    "catalog": "Import a country or industry package, or export one of your categories as JSON to share.",
    "backup": "Download or restore a zip of the database, Send library, and .env, or roll back the last app.",
    "update": "Check GitHub Releases and install a newer zip.",
    "about": "What NewsCast is, who wrote it, and the version running here.",
}


def normalize_settings_tab(value: str | None) -> str:
    key = SETTINGS_TAB_ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())
    return key if key in SETTINGS_TAB_KEYS else "device"


def settings_path(tab: str | None = "device") -> str:
    return f"/settings?tab={normalize_settings_tab(tab)}"


def _story_categories(db: Session) -> dict[str, str]:
    return {feed.name: feed.category for feed in db.query(Feed).all()}


def _base_context(request: Request, db: Session, active: str) -> dict:
    ingest = snapshot()
    llm = settings.llm_config(db)
    return {
        "request": request,
        "active": active,
        "ingest": ingest,
        "has_openai_key": llm.provider == "openai" and llm.ready,
        "llm_ready": llm.ready,
        "llm_provider": llm.provider,
        "using_factory_admin": settings.using_factory_admin(db),
        "app_version": __version__,
        "asset_rev": __asset_rev__,
        "app_author": __author__,
        "homescreen_name": hostname.homescreen_name(db),
        "favicons": favicon.map_for_feeds(db.query(Feed).all()),
    }


@public.get("/login")
def login_page(request: Request, db: Annotated[Session, Depends(get_db)], next: str = "/"):
    nxt = safe_next(next)
    if is_signed_in(request):
        return RedirectResponse(nxt, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "next": nxt,
            "error": None,
            "username": "",
            "using_factory_admin": settings.using_factory_admin(db),
            "app_version": __version__,
            "homescreen_name": hostname.homescreen_name(db),
        },
    )


@public.get("/manifest.webmanifest")
def web_manifest(db: Annotated[Session, Depends(get_db)]):
    name = hostname.homescreen_name(db)
    return JSONResponse(
        {
            "name": name,
            "short_name": name,
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f4efe4",
            "theme_color": "#f4efe4",
            "icons": [
                {
                    "src": "/static/icons/apple-touch-icon.svg",
                    "type": "image/svg+xml",
                    "sizes": "any",
                    "purpose": "any",
                }
            ],
        },
        media_type="application/manifest+json",
    )


@public.post("/login")
def login_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    nxt = safe_next(next)
    if credentials_match(db, username.strip(), password):
        response = RedirectResponse(nxt, status_code=303)
        attach_session(response, username.strip())
        return response
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "next": nxt,
            "error": "That username or password is not right.",
            "username": username.strip(),
            "using_factory_admin": settings.using_factory_admin(db),
            "app_version": __version__,
            "homescreen_name": hostname.homescreen_name(db),
        },
    )


@public.api_route("/logout", methods=["GET", "POST"])
def logout_submit():
    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response


@router.get("/")
def briefing_page(request: Request, db: Annotated[Session, Depends(get_db)], day: str = "today"):
    briefing_day = normalize_briefing_day(day)
    stories = [story for story in current_stories(db, day=briefing_day) if not story.saved]
    categories = _story_categories(db)
    icons = favicon.map_for_feeds(db.query(Feed).all())
    icons.update(favicon.map_for_stories(stories))
    for story in stories:
        story.category = categories.get(story.source_name, "news")
        story.published_label = format_published(story.published_at or story.created_at)
        story.favicon = favicon.lookup(
            icons,
            story.source_name,
            story.canonical_url,
            favicon.host_key(story.canonical_url),
            favicon.host_key(favicon.homepage_url(story.canonical_url)),
        )
    return templates.TemplateResponse(
        request,
        "briefing.html",
        {
            **_base_context(request, db, "briefing"),
            "stories": stories,
            "category_labels": category_labels(db),
            "retention_days": env.story_retention_days,
            "briefing_day": briefing_day,
        },
    )


@router.post("/stories/{story_id}/favourite")
def toggle_favourite(
    story_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    day: Annotated[str, Form()] = "today",
):
    story = db.get(Story, story_id)
    nxt = briefing_path(day)
    if story is None:
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Story not found."}, status_code=404)
        return RedirectResponse(nxt, status_code=303)
    story.favourited = not bool(story.favourited)
    db.commit()
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "favourited": story.favourited,
                "message": "Saved to favourites." if story.favourited else "Removed from favourites.",
            }
        )
    return RedirectResponse(nxt, status_code=303)


@router.post("/stories/{story_id}/longread")
def save_story_longread(
    story_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    day: Annotated[str, Form()] = "today",
):
    story = db.get(Story, story_id)
    nxt = briefing_path(day)
    if story is None:
        return _form_error(request, "Story not found.", nxt, 404)
    if story.saved:
        if _wants_json(request):
            return JSONResponse({"ok": True, "saved": True, "message": "Already on Saved."})
        return RedirectResponse("/saved", status_code=303)
    try:
        saved_articles.save_article(db, story.canonical_url, "7", "", origin=saved_articles.ORIGIN_BRIEFING)
    except ValueError as exc:
        return _form_error(request, str(exc), nxt)
    if _wants_json(request):
        return JSONResponse({"ok": True, "saved": True, "message": "Saved as a long-read."})
    return RedirectResponse("/saved", status_code=303)


@router.get("/search")
def search_page(request: Request, db: Annotated[Session, Depends(get_db)], q: str = ""):
    query = (q or "").strip()
    stories = search_stories(db, query) if query else []
    categories = _story_categories(db)
    icons = favicon.map_for_feeds(db.query(Feed).all())
    icons.update(favicon.map_for_stories(stories))
    for story in stories:
        story.category = categories.get(story.source_name, "news")
        story.published_label = format_published(story.published_at or story.created_at)
        story.favicon = favicon.lookup(
            icons,
            story.source_name,
            story.canonical_url,
            favicon.host_key(story.canonical_url),
            favicon.host_key(favicon.homepage_url(story.canonical_url)),
        )
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            **_base_context(request, db, "search"),
            "query": query,
            "stories": stories,
        },
    )


@router.get("/saved")
def saved_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    items = current_saved_stories(db)
    icons = favicon.map_for_feeds(db.query(Feed).all())
    icons.update(favicon.map_for_stories(items))
    for item in items:
        item.favicon = favicon.lookup(
            icons,
            item.source_name,
            item.canonical_url,
            favicon.host_key(item.canonical_url),
            favicon.host_key(favicon.homepage_url(item.canonical_url)),
        )
        item.origin_label = saved_articles.saved_origin_label(getattr(item, "saved_origin", None))
    return templates.TemplateResponse(
        request,
        "saved.html",
        {
            **_base_context(request, db, "saved"),
            "items": items,
            "keep_options": saved_articles.KEEP_OPTIONS,
        },
    )


@router.post("/saved")
def save_article_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    url: Annotated[str, Form()] = "",
    keep_days: Annotated[str, Form()] = "7",
    custom_date: Annotated[str, Form()] = "",
):
    try:
        story = saved_articles.save_article(db, url, keep_days, custom_date, origin=saved_articles.ORIGIN_MANUAL)
    except ValueError as exc:
        return _form_error(request, str(exc), "/saved")
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": f"Saved “{story.title}” for later."})
    return RedirectResponse("/saved", status_code=303)


@router.post("/saved/{story_id}/delete")
def delete_saved_article(story_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    story = db.get(Story, story_id)
    if story is None or not story.saved:
        return _form_error(request, "That saved article was not found.", "/saved", 404)
    db.delete(story)
    db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed from Saved."})
    return RedirectResponse("/saved", status_code=303)


@router.get("/feeds")
def feeds_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    feeds = db.query(Feed).order_by(Feed.enabled.desc(), Feed.name.asc()).all()
    now = utcnow()
    for feed in feeds:
        feed.is_muted = feed_is_muted(feed, now)
        feed.health = feed_health(feed, now)
        feed.health_label = feed_health_label(feed, now)
    return templates.TemplateResponse(
        request,
        "feeds.html",
        {
            **_base_context(request, db, "feeds"),
            "feeds": feeds,
            "category_labels": category_labels(db),
            "refresh_intervals": settings.REFRESH_INTERVALS,
            "global_interval": settings.get_int(db, "ingest_interval_minutes", env.ingest_interval_minutes),
            "global_interval_label": settings.format_interval_short(
                settings.get_int(db, "ingest_interval_minutes", env.ingest_interval_minutes)
            ),
        },
    )


@router.get("/catalog")
def catalog_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request,
        "catalog.html",
        {
            **_base_context(request, db, "catalog"),
            "catalog": grouped_catalog(db),
            "custom_feeds": db.query(Feed).filter(Feed.catalog_id.is_(None)).order_by(Feed.name.asc()).all(),
            "category_labels": category_labels(db),
        },
    )


@router.get("/status")
def status_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    story_count = db.query(Story).count()
    share_url = hostname.get_share_url(db)
    reader = reader_push.snapshot(db, probe=False)
    delivery = delivery_status(db)
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            **_base_context(request, db, "status"),
            "story_count": story_count,
            "reader": reader,
            "delivery": delivery,
            "public_base_url": hostname.get_public_base_url(db),
            "share_url": share_url,
            "lan_url": hostname.get_lan_url(),
            "qr_svg": qrcode.svg_for(share_url),
            "request_base_url": str(request.base_url).rstrip("/"),
            "instance_name": settings.get_value(db, "instance_name"),
            "x3_catalog_login": settings.catalog_login_enabled(db),
            "x3_catalog_username": settings.catalog_username(db),
            "x3_token_set": bool(settings.get_value(db, "x3_sync_token")),
            "update_check": update.last_check(db),
            "paper": paper_status(db),
            "reader_device": settings.reader_device(db),
            "health": status_health(db),
        },
    )


@router.get("/library")
def library_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            **_base_context(request, db, "library"),
            "library_files": _library_items(db),
            "reader": reader_push.snapshot(db, probe=False),
        },
    )


@router.get("/settings")
def settings_page(request: Request, db: Annotated[Session, Depends(get_db)], tab: str = "device"):
    settings_tab = normalize_settings_tab(tab)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            **_base_context(request, db, "settings"),
            "settings_tab": settings_tab,
            "settings_tabs": SETTINGS_TABS,
            "settings_save_tabs": SETTINGS_SAVE_TABS,
            "settings_lede": SETTINGS_LEDES[settings_tab],
            "settings_ledes": SETTINGS_LEDES,
            "admin_username": settings.get_value(db, "admin_username"),
            "openai_key": settings.secret_hint(db, "openai_api_key"),
            "openai_model": settings.get_value(db, "openai_model"),
            "openai_models": settings.OPENAI_MODELS,
            "openai_model_known": settings.get_value(db, "openai_model") in settings.OPENAI_MODEL_IDS,
            "llm_provider": settings.normalize_provider(settings.get_value(db, "llm_provider")),
            "ollama_base_url": settings.normalize_ollama_root(settings.get_value(db, "ollama_base_url")),
            "ollama_model": settings.get_value(db, "ollama_model"),
            "instance_name": settings.get_value(db, "instance_name"),
            "x3_token": settings.secret_hint(db, "x3_sync_token"),
            "x3_catalog_login": settings.catalog_login_enabled(db),
            "x3_catalog_username": settings.catalog_username(db),
            "x3_device_id": settings.get_value(db, "x3_device_id"),
            "device_hostname": hostname.normalize_hostname(settings.get_value(db, "device_hostname")),
            "app_port": env.port,
            "refresh_intervals": settings.REFRESH_INTERVALS,
            "global_interval": settings.get_int(db, "ingest_interval_minutes", env.ingest_interval_minutes),
            "ingest_active_start": settings.get_value(db, "ingest_active_start"),
            "ingest_active_end": settings.get_value(db, "ingest_active_end"),
            "briefing_limits": settings.BRIEFING_LIMITS,
            "briefing_limit": settings.briefing_limit(db),
            "importance_min_choices": settings.IMPORTANCE_MIN_CHOICES,
            "briefing_min_importance": settings.briefing_min_importance(db),
            "briefing_publish_at": briefing_publish_at(db),
            "github_repo": update.repo_from_db(db),
            "update_check": update.last_check(db),
            "categories": list_categories(db),
            "export_categories": list_categories(db),
            "latest_backup": backup.latest_backup(),
            "keyword_include": settings.get_value(db, "keyword_include"),
            "keyword_exclude": settings.get_value(db, "keyword_exclude"),
            "reader_device": settings.reader_device(db),
            "reader_devices": settings.READER_DEVICES,
            "reader_host": settings.get_value(db, "reader_host"),
            "reader_upload_path": settings.get_value(db, "reader_upload_path"),
            "reader_push_when_online": settings.reader_push_enabled(db),
            "reader_ssh_port": settings.reader_ssh_port(db),
            "reader_ssh_user": settings.reader_ssh_user(db),
            "reader_ssh_password": settings.secret_hint(db, "reader_ssh_password"),
            "reader_title_pattern": paper_naming.reader_title_pattern(db),
            "reader_date_format": paper_naming.reader_date_format(db),
            "reader_date_formats": paper_naming.DATE_FORMATS,
            "reader_title_tokens": paper_naming.TITLE_TOKENS,
            "reader_date_previews": paper_naming.date_format_previews(date.today()),
            "reader_paper_label": settings.get_value(db, "reader_paper_label"),
            "reader_title_preview": paper_naming.paper_display_title(db, date.today()),
            "delivery": delivery_status(db),
        },
    )


@router.post("/library")
def upload_library_file(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str, Form()] = "",
    file: UploadFile = File(...),
):
    data = file.file.read(library.MAX_UPLOAD_BYTES + 1)
    try:
        library.add_library_file(db, file.filename or "document", data, title)
    except ValueError as exc:
        return _form_error(request, str(exc), "/library")
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Queued for the next reader sync. Not summarised."})
    return RedirectResponse("/library", status_code=303)


@router.post("/library/{file_id}/push")
def push_library_file(file_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    item = db.get(LibraryFile, file_id)
    if item is None:
        return _form_error(request, "File not found.", "/library", status_code=404)
    try:
        library.enqueue_library_file(db, item)
    except FileNotFoundError as exc:
        return _form_error(request, str(exc), "/library")
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Queued for the next reader sync."})
    return RedirectResponse("/library", status_code=303)


@router.post("/reader/poll")
def poll_reader(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/library"):
    nxt = safe_next(next)
    if nxt not in {"/status", "/library"}:
        nxt = "/library"
    host = reader_push.reader_host(db)
    online = reader_push.reader_reachable(host, db=db)
    reader_push.remember_probe(host, online)
    message = f"{host} is {'online' if online else 'asleep'}."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message, "online": online, "host": host})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/push")
def push_reader_now(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = safe_next(next)
    if nxt not in {"/status", "/library"}:
        nxt = "/status"
    reader_push.enqueue_briefing_and_library(db)
    result = reader_push.flush_pending(db)
    if result.get("online"):
        message = f"Pushed {result.get('uploaded', 0)} file{'s' if result.get('uploaded') != 1 else ''} to the reader."
    else:
        message = "Reader is asleep. Files are queued until it is on Wi-Fi."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message, **result})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/queue")
def queue_reader_later(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = safe_next(next)
    if nxt not in {"/status", "/library"}:
        nxt = "/status"
    tasks = reader_push.enqueue_briefing_and_library(db)
    message = f"Queued {len(tasks)} file{'s' if len(tasks) != 1 else ''} for when the reader is on Wi-Fi."
    if not paper_status(db)["published"]:
        message += " Today's paper is not published yet."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message, "pending": len(reader_push.pending_crosspoint(db))})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/queue/{task_id}/cancel")
def cancel_reader_queue(
    task_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    next: Annotated[str, Form()] = "/status",
):
    nxt = safe_next(next)
    if nxt not in {"/status", "/library"}:
        nxt = "/status"
    if not reader_push.cancel_pending(db, task_id):
        return _form_error(request, "That queued file was already gone.", nxt)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed from the queue."})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/publish")
def publish_reader_paper(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = safe_next(next)
    if nxt not in {"/status", "/library"}:
        nxt = "/status"
    publish_daily_briefing(db, overwrite=True)
    if settings.reader_push_enabled(db):
        reader_push.enqueue_frozen_briefing(db)
    message = "Published today's paper."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message})
    return RedirectResponse(nxt, status_code=303)


@router.post("/library/{file_id}/delete")
def delete_library_file_form(file_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    item = db.get(LibraryFile, file_id)
    if item:
        library.delete_library_file(db, item)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed from the library."})
    return RedirectResponse("/library", status_code=303)


@router.get("/api/ollama/models")
def ollama_models(
    db: Annotated[Session, Depends(get_db)],
    base_url: str = "",
):
    from app.services.summarize import list_ollama_models

    url = (base_url or settings.get_value(db, "ollama_base_url")).strip()
    try:
        models = list_ollama_models(url)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "message": "Could not reach Ollama. Is it running on that URL?"},
            status_code=502,
        )
    return JSONResponse({"ok": True, "models": models})


@router.post("/ingest")
def ingest_form(request: Request):
    result = start_ingest(force=True)
    if _wants_json(request):
        return JSONResponse(result)
    return RedirectResponse("/", status_code=303)


@router.post("/feeds")
def create_feed_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form()],
    url: Annotated[str, Form()],
    category: Annotated[str, Form()] = "news",
    source_type: Annotated[str, Form()] = "auto",
    summarize: Annotated[str, Form()] = "1",
    translate: Annotated[str, Form()] = "0",
    next: Annotated[str, Form()] = "/feeds",
):
    nxt = safe_next(next)
    if nxt not in {"/feeds", "/catalog"}:
        nxt = "/feeds"
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Enter a valid http(s) site or feed URL."}, status_code=400)
        return RedirectResponse(nxt, status_code=303)
    kind = source_type if source_type in {"auto", "rss", "webpage"} else "auto"
    existing = db.query(Feed).filter(Feed.url == url.strip()).one_or_none()
    if existing is None:
        db.add(
            Feed(
                name=name.strip() or parsed.netloc,
                url=url.strip(),
                category=category,
                type=kind,
                enabled=True,
                summarize=summarize != "0",
                translate=translate != "0",
            )
        )
        db.commit()
        added = db.query(Feed).filter(Feed.url == url.strip()).one_or_none()
        if added:
            favicon.capture_for_feed_async(added.id)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Source added."})
    return RedirectResponse(nxt, status_code=303)


@router.post("/feeds/{feed_id}/schedule")
def save_feed_schedule(
    feed_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    schedule_mode: Annotated[str, Form()] = "global",
    interval_minutes: Annotated[str, Form()] = "",
    summarize: Annotated[str, Form()] = "1",
    translate: Annotated[str, Form()] = "0",
    keyword_include: Annotated[str, Form()] = "",
    keyword_exclude: Annotated[str, Form()] = "",
):
    feed = db.get(Feed, feed_id)
    if feed is None:
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Feed not found."}, status_code=404)
        return RedirectResponse("/feeds", status_code=303)
    feed.schedule_mode = schedule_mode if schedule_mode in {"global", "custom"} else "global"
    if feed.schedule_mode == "custom":
        try:
            minutes = int(interval_minutes)
            feed.interval_minutes = minutes if minutes > 0 else 60
        except ValueError:
            feed.interval_minutes = 60
    feed.summarize = summarize != "0"
    feed.translate = translate != "0"
    feed.keyword_include = keyword_include.strip()
    feed.keyword_exclude = keyword_exclude.strip()
    db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Source settings saved."})
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/mute")
def mute_feed(feed_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed is None:
        return _form_error(request, "Feed not found.", "/feeds", 404)
    feed.muted_until = utcnow() + timedelta(hours=24)
    db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": f"Muted {feed.name} for 24 hours."})
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/unmute")
def unmute_feed(feed_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed is None:
        return _form_error(request, "Feed not found.", "/feeds", 404)
    feed.muted_until = None
    db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": f"Unmuted {feed.name}."})
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/refresh")
def refresh_one_feed(feed_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed is None:
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Feed not found."}, status_code=404)
        return RedirectResponse("/feeds", status_code=303)
    result = start_ingest(force=True, feed_id=feed_id)
    if _wants_json(request):
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/toggle")
def toggle_feed(feed_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    feed = db.get(Feed, feed_id)
    if feed:
        feed.enabled = not feed.enabled
        db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "enabled": bool(feed and feed.enabled)})
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/delete")
def delete_feed_form(
    feed_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    next: Annotated[str, Form()] = "/feeds",
):
    feed = db.get(Feed, feed_id)
    if feed:
        db.delete(feed)
        db.commit()
    nxt = next if next in {"/feeds", "/catalog"} else "/feeds"
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed the feed."})
    return RedirectResponse(nxt, status_code=303)


@router.post("/catalog/{catalog_id}/add")
def add_catalog_form(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.routers.feeds import add_recommended

    add_recommended(catalog_id, db)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Feed enabled."})
    return RedirectResponse("/catalog", status_code=303)


@router.post("/catalog/{catalog_id}/remove")
def remove_catalog_form(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services.catalog import find_catalog_item

    item = find_catalog_item(catalog_id)
    feed = None
    if item:
        feed = (
            db.query(Feed)
            .filter((Feed.catalog_id == catalog_id) | (Feed.url == item["url"]))
            .one_or_none()
        )
    else:
        feed = db.query(Feed).filter(Feed.catalog_id == catalog_id).one_or_none()
    if feed:
        db.delete(feed)
        db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed the feed."})
    return RedirectResponse("/catalog", status_code=303)


@router.post("/settings")
def save_settings(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin_username: Annotated[str, Form()] = "",
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    new_password_confirm: Annotated[str, Form()] = "",
    openai_api_key: Annotated[str, Form()] = "",
    clear_openai_api_key: Annotated[str, Form()] = "",
    openai_model: Annotated[str, Form()] = "",
    openai_model_custom: Annotated[str, Form()] = "",
    llm_provider: Annotated[str, Form()] = "openai",
    ollama_base_url: Annotated[str, Form()] = "",
    ollama_model: Annotated[str, Form()] = "",
    instance_name: Annotated[str, Form()] = "",
    x3_sync_token: Annotated[str, Form()] = "",
    clear_x3_sync_token: Annotated[str, Form()] = "",
    x3_catalog_login: Annotated[str, Form()] = "",
    x3_catalog_username: Annotated[str, Form()] = "",
    x3_device_id: Annotated[str, Form()] = "",
    device_hostname: Annotated[str, Form()] = "",
    ingest_interval_minutes: Annotated[str, Form()] = "",
    ingest_active_start: Annotated[str, Form()] = "",
    ingest_active_end: Annotated[str, Form()] = "",
    briefing_limit: Annotated[str, Form()] = "",
    briefing_min_importance: Annotated[str, Form()] = "",
    briefing_publish_at: Annotated[str, Form()] = "",
    github_repo: Annotated[str, Form()] = "",
    keyword_include: Annotated[str, Form()] = "",
    keyword_exclude: Annotated[str, Form()] = "",
    reader_host: Annotated[str, Form()] = "",
    reader_upload_path: Annotated[str, Form()] = "",
    reader_push_when_online: Annotated[str, Form()] = "",
    reader_device: Annotated[str, Form()] = "xteink",
    reader_ssh_port: Annotated[str, Form()] = "",
    reader_ssh_user: Annotated[str, Form()] = "",
    reader_ssh_password: Annotated[str, Form()] = "",
    clear_reader_ssh_password: Annotated[str, Form()] = "",
    reader_title_pattern: Annotated[str, Form()] = "",
    reader_date_format: Annotated[str, Form()] = "iso",
    reader_paper_label: Annotated[str, Form()] = "",
    settings_tab: Annotated[str, Form()] = "device",
):
    tab = normalize_settings_tab(settings_tab)
    reauth = False
    current_user, current_pass = settings.get_admin_credentials(db)
    changing_password = bool(new_password.strip())
    changing_username = bool(admin_username.strip()) and admin_username.strip() != current_user

    if changing_password or changing_username:
        if current_password != current_pass:
            return _settings_error(request, "Current password is incorrect.", tab)
        if changing_password:
            if new_password != new_password_confirm:
                return _settings_error(request, "New passwords do not match.", tab)
            if len(new_password) < 4:
                return _settings_error(request, "New password must be at least 4 characters.", tab)
            settings.set_value(db, "admin_password", new_password)
            reauth = True
        if changing_username:
            settings.set_value(db, "admin_username", admin_username.strip())
            reauth = True

    if clear_openai_api_key:
        settings.clear_value(db, "openai_api_key")
    elif openai_api_key.strip():
        settings.set_value(db, "openai_api_key", openai_api_key.strip())

    chosen_model = openai_model.strip()
    if chosen_model == "other":
        chosen_model = openai_model_custom.strip()
    if chosen_model:
        settings.set_value(db, "openai_model", chosen_model)

    provider = settings.normalize_provider(llm_provider)
    settings.set_value(db, "llm_provider", provider)
    ollama_root = settings.normalize_ollama_root(ollama_base_url)
    if not ollama_root.startswith(("http://", "https://")):
        return _settings_error(request, "Ollama URL must start with http:// or https://", tab)
    settings.set_value(db, "ollama_base_url", ollama_root)
    if ollama_model.strip():
        settings.set_value(db, "ollama_model", ollama_model.strip())
    else:
        settings.clear_value(db, "ollama_model")
    if instance_name.strip():
        settings.set_value(db, "instance_name", instance_name.strip()[:80])
    else:
        settings.clear_value(db, "instance_name")

    if clear_x3_sync_token:
        settings.clear_value(db, "x3_sync_token")
    elif x3_sync_token.strip():
        settings.set_value(db, "x3_sync_token", x3_sync_token.strip())

    settings.set_value(db, "x3_catalog_login", "1" if x3_catalog_login else "0")
    if x3_catalog_username.strip():
        settings.set_value(db, "x3_catalog_username", x3_catalog_username.strip()[:80])
    else:
        settings.clear_value(db, "x3_catalog_username")
    settings.set_value(db, "x3_device_id", x3_device_id.strip())
    settings.set_value(db, "keyword_include", keyword_include.strip())
    settings.set_value(db, "keyword_exclude", keyword_exclude.strip())
    device = settings.normalize_reader_device(reader_device)
    settings.set_value(db, "reader_device", device)
    host = reader_host.strip().removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        settings.set_value(db, "reader_host", host)
    elif device == "kobo":
        settings.clear_value(db, "reader_host")
    else:
        settings.set_value(db, "reader_host", settings.DEFAULT_XTEINK_HOST)
    default_folder = settings.DEFAULT_KOBO_FOLDER if device == "kobo" else settings.DEFAULT_XTEINK_FOLDER
    folder = reader_upload_path.strip() or default_folder
    if not folder.startswith("/"):
        folder = "/" + folder
    settings.set_value(db, "reader_upload_path", folder.rstrip("/") or default_folder)
    settings.set_value(db, "reader_push_when_online", "1" if reader_push_when_online else "0")
    if reader_ssh_port.strip():
        try:
            port = int(reader_ssh_port)
        except ValueError:
            return _settings_error(request, "SSH port must be a number.", tab)
        if not 1 <= port <= 65535:
            return _settings_error(request, "SSH port must be between 1 and 65535.", tab)
        settings.set_value(db, "reader_ssh_port", str(port))
    user = reader_ssh_user.strip() or settings.DEFAULT_KOBO_SSH_USER
    settings.set_value(db, "reader_ssh_user", user)
    if clear_reader_ssh_password:
        settings.clear_value(db, "reader_ssh_password")
    elif reader_ssh_password.strip():
        settings.set_value(db, "reader_ssh_password", reader_ssh_password.strip())
    settings.set_value(db, "reader_title_pattern", paper_naming.normalize_title_pattern(reader_title_pattern))
    settings.set_value(db, "reader_date_format", paper_naming.normalize_date_format(reader_date_format))
    label = paper_naming.normalize_paper_label(reader_paper_label)
    if label:
        settings.set_value(db, "reader_paper_label", label)
    else:
        settings.clear_value(db, "reader_paper_label")
    wanted_host = hostname.normalize_hostname(device_hostname)
    if wanted_host:
        if not hostname.valid_hostname(wanted_host):
            return _settings_error(request, "Hostname must be letters, digits, or hyphens.", tab)
        settings.set_value(db, "device_hostname", wanted_host)
        hostname.apply_os_hostname(wanted_host)
    else:
        settings.clear_value(db, "device_hostname")
    if ingest_interval_minutes.strip():
        try:
            minutes = int(ingest_interval_minutes)
            if minutes > 0:
                settings.set_value(db, "ingest_interval_minutes", str(minutes))
        except ValueError:
            return _settings_error(request, "Refresh interval must be a number of minutes.", tab)
    start_clock = normalize_optional_clock(ingest_active_start)
    end_clock = normalize_optional_clock(ingest_active_end)
    if start_clock:
        settings.set_value(db, "ingest_active_start", start_clock)
    else:
        settings.clear_value(db, "ingest_active_start")
    if end_clock:
        settings.set_value(db, "ingest_active_end", end_clock)
    else:
        settings.clear_value(db, "ingest_active_end")
    if briefing_limit.strip():
        try:
            limit = int(briefing_limit)
        except ValueError:
            return _settings_error(request, "Briefing size must be a number of stories.", tab)
        if limit not in settings.BRIEFING_LIMIT_VALUES:
            return _settings_error(request, "Choose 10, 20, 30, 40, or 50 stories.", tab)
        settings.set_value(db, "briefing_limit", str(limit))
    if briefing_min_importance.strip():
        try:
            minimum = int(briefing_min_importance)
        except ValueError:
            return _settings_error(request, "Paper importance must be a number from 1 to 5.", tab)
        if minimum not in settings.IMPORTANCE_MIN_VALUES:
            return _settings_error(request, "Choose a paper importance threshold from 1 to 5.", tab)
        settings.set_value(db, "briefing_min_importance", str(minimum))
    if briefing_publish_at.strip():
        settings.set_value(db, "briefing_publish_at", normalize_publish_at(briefing_publish_at))
    repo = update.normalize_repo(github_repo)
    if github_repo.strip() and not repo:
        return _settings_error(request, "GitHub repository must look like owner/NewsCast.", tab)
    if repo:
        settings.set_value(db, "github_repo", repo)
    else:
        settings.clear_value(db, "github_repo")

    payload = {"ok": True, "message": "Settings saved.", "reauth": reauth}
    if _wants_json(request):
        response: JSONResponse | RedirectResponse = JSONResponse(payload)
    elif reauth:
        response = RedirectResponse("/login", status_code=303)
    else:
        response = RedirectResponse(settings_path(tab), status_code=303)
    if reauth:
        clear_session(response)
    return response


@router.post("/settings/updates/check")
def check_updates(request: Request, db: Annotated[Session, Depends(get_db)]):
    result = update.check_latest(db)
    if _wants_json(request):
        return JSONResponse({"ok": bool(result.get("ok")), "message": result.get("message") or "Checked GitHub."})
    return RedirectResponse(settings_path("update"), status_code=303)


@router.post("/settings/updates/install")
def install_update(request: Request, db: Annotated[Session, Depends(get_db)]):
    try:
        result = update.install_latest(db)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("update"))
    except Exception as exc:
        logger.exception("update install failed")
        return _form_error(request, _install_error_message(exc), settings_path("update"))
    update.schedule_restart()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": result.get("message") or "Installing…"})
    return RedirectResponse(settings_path("update"), status_code=303)


@router.post("/settings/updates/rollback")
def rollback_update(request: Request):
    try:
        update.rollback_code()
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("backup"))
    except Exception as exc:
        logger.exception("update rollback failed")
        return _form_error(request, _install_error_message(exc), settings_path("backup"))
    update.schedule_restart()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Rolled back to the previous app. Restarting…"})
    return RedirectResponse(settings_path("backup"), status_code=303)


@router.get("/settings/backup")
def download_backup():
    path = backup.write_backup()
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/settings/backup/restore")
def restore_backup_form(
    request: Request,
    file: UploadFile = File(...),
):
    data = file.file.read()
    try:
        backup.restore_backup(data)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("backup"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Backup restored. Settings and sources are back."})
    return RedirectResponse(settings_path("backup"), status_code=303)


@router.post("/settings/categories")
def add_category_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    label: Annotated[str, Form()] = "",
):
    try:
        category_service.add_category(db, label)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("categories"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Category added."})
    return RedirectResponse(settings_path("categories"), status_code=303)


@router.post("/settings/categories/{key}/rename")
def rename_category_form(
    key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    label: Annotated[str, Form()] = "",
):
    try:
        category_service.rename_category(db, key, label)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("categories"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Category renamed."})
    return RedirectResponse(settings_path("categories"), status_code=303)


@router.post("/settings/categories/{key}/delete")
def delete_category_form(key: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    try:
        category_service.delete_category(db, key)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("categories"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Category removed. Sources moved to World News."})
    return RedirectResponse(settings_path("categories"), status_code=303)


@router.post("/catalog/packages/import")
def import_package_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
):
    raw = file.file.read()
    next_path = settings_path("catalog")
    try:
        result = package_service.import_package(db, json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        return _form_error(request, str(exc), next_path)
    except Exception:
        return _form_error(request, "That file is not valid package JSON.", next_path)
    added = result["created"]
    name = result["package"]["name"]
    message = f"Imported {name}. {added} new source{'s' if added != 1 else ''} added to the catalog."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message})
    return RedirectResponse(next_path, status_code=303)


@router.get("/catalog/packages/export")
def export_package(db: Annotated[Session, Depends(get_db)], category: str = ""):
    try:
        package = package_service.export_category(db, category, catalog_with_status(db))
    except ValueError:
        return RedirectResponse(settings_path("catalog"), status_code=303)
    filename = f"{package['id']}.json"
    return Response(
        json.dumps(package, indent=2) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _library_items(db: Session) -> list[dict]:
    items = db.query(LibraryFile).order_by(LibraryFile.created_at.desc()).all()
    return [
        {
            "id": item.id,
            "title": item.title,
            "original_name": item.original_name,
            "size": library.pretty_size(item.size),
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
        }
        for item in items
    ]


def _install_error_message(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        detail = exc.strerror or str(exc)
        return f"Could not replace app files ({detail}). Close other NewsCast windows and try again."
    return f"Could not install the update: {exc}"


def _form_error(request: Request, message: str, redirect: str, status_code: int = 400):
    if _wants_json(request):
        return JSONResponse({"ok": False, "message": message}, status_code=status_code)
    return RedirectResponse(redirect, status_code=303)


def _settings_error(request: Request, message: str, tab: str | None = "device"):
    if _wants_json(request):
        return JSONResponse({"ok": False, "message": message}, status_code=400)
    return RedirectResponse(settings_path(tab), status_code=303)


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.headers.get("x-requested-with") == "fetch"
