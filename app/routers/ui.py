import json
import logging
from datetime import date, timedelta
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import __asset_rev__, __github__, __version__
from app.auth import (
    attach_session,
    clear_login_failures,
    clear_session,
    credentials_match,
    effective_user_id,
    is_signed_in,
    login_rate_limited,
    record_login_failure,
    require_admin,
    request_is_https,
    resolve_login_identity,
    safe_next,
    session_from_request,
)
from app.config import ROOT_DIR, env
from app.db import get_db
from app.models import Feed, LibraryFile, Story, SyncTask, User, utcnow
from app.services import backup, favicon, hostname, i18n, library, ntfy, paper_naming, passwords, qrcode, reader_config, reader_push, settings, tls, translate, update
from app.services import users as users_service
from app.services import user_settings as user_settings_service
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
from app.services import catalog as catalog_service
from app.services.categories import category_labels, list_categories
from app.services.ingest import snapshot, start_ingest
from app.services import categories as category_service
from app.services import packages as package_service

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
logger = logging.getLogger("newscast.ui")

SETTINGS_TABS = (
    ("device", "General"),
    ("publication", "Publication"),
    ("schedule", "Schedule"),
    ("filters", "Filters"),
    ("translation", "Translation"),
    ("llm", "LLM"),
    ("reader", "Reader"),
    ("notifications", "Notifications"),
    ("categories", "Categories"),
    ("catalog", "Catalog"),
    ("users", "Users"),
    ("backup", "Backup/Restore"),
    ("update", "Update"),
    ("about", "About"),
)
SETTINGS_TAB_KEYS = {key for key, _label in SETTINGS_TABS}
# Instance / household controls — non-admins never see these tabs.
ADMIN_ONLY_SETTINGS_TABS = frozenset(
    {"schedule", "llm", "catalog", "users", "backup", "update"}
)
USER_SETTINGS_TABS = tuple(
    (key, label) for key, label in SETTINGS_TABS if key not in ADMIN_ONLY_SETTINGS_TABS
)
SETTINGS_TAB_ALIASES = {"access": "device"}
SETTINGS_SAVE_TABS = {
    "device",
    "publication",
    "schedule",
    "filters",
    "translation",
    "llm",
    "reader",
    "notifications",
    "update",
}
SETTINGS_LEDES = {
    "device": "Colour palette, admin login, hostname, HTTPS, and the Home or Work name for this copy.",
    "publication": "How the paper is named, how many stories it keeps, category mix, and which topics get their own OPDS papers.",
    "schedule": "How often sources refresh, and when today’s newspaper freezes for the reader.",
    "filters": "Words to keep or drop across every source. A feed can add more on Feeds.",
    "translation": "Choose the language Translate feeds land in, and whether Google or your LLM does the work.",
    "llm": "OpenAI or Ollama for short summaries and optional translation. Refresh still works without a model.",
    "reader": "Your reader device: Xteink with CrossPoint, or Kobo with KOReader. Host, upload folder, and push when on Wi-Fi are per account.",
    "notifications": "Phone alerts via ntfy when the paper is published or reaches the reader.",
    "categories": "Built-in groups stay. Add a country or topic, then fill it from Catalog.",
    "catalog": "Import a country or industry package, or export one of your categories as JSON to share.",
    "users": "Household accounts. Edit each person’s access, reset passwords, or show a one-time login QR.",
    "backup": "Download or restore a zip of the database, Send library, and .env, or roll back the last app.",
    "update": "Check GitHub Releases and install a newer zip.",
    "about": "What NewsCast is, who wrote it, and the version running here.",
}


def normalize_settings_tab(value: str | None) -> str:
    key = SETTINGS_TAB_ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())
    return key if key in SETTINGS_TAB_KEYS else "device"


def settings_tabs_for(
    role: str | None,
    *,
    can_use_ntfy: bool = False,
) -> tuple[tuple[str, str], ...]:
    if role == "admin":
        return SETTINGS_TABS
    tabs = USER_SETTINGS_TABS
    if not can_use_ntfy:
        tabs = tuple((key, label) for key, label in tabs if key != "notifications")
    return tabs


def normalize_settings_tab_for_role(
    value: str | None,
    role: str | None,
    *,
    can_use_ntfy: bool = False,
) -> str:
    key = normalize_settings_tab(value)
    allowed = {tab for tab, _ in settings_tabs_for(role, can_use_ntfy=can_use_ntfy)}
    return key if key in allowed else "device"


def _session_can_use_ntfy(db: Session, request: Request) -> bool:
    session = session_from_request(request)
    if not session:
        return False
    if session.role == "admin":
        return True
    user = db.get(User, session.user_id) if session.user_id else None
    return users_service.user_may_use_ntfy(user)


def _session_can_view_status(db: Session, request: Request) -> bool:
    session = session_from_request(request)
    if not session:
        return False
    if session.role == "admin":
        return True
    user = db.get(User, session.user_id) if session.user_id else None
    return users_service.user_may_view_status(user)


def _reader_redirect_next(db: Session, request: Request, next_value: str, *, default: str = "/library") -> str:
    nxt = safe_next(next_value)
    if nxt not in {"/status", "/library"}:
        nxt = default
    if nxt == "/status" and not _session_can_view_status(db, request):
        return "/library"
    return nxt


def settings_path(tab: str | None = "device") -> str:
    return f"/settings?tab={normalize_settings_tab(tab)}"


def _current_user_id(request: Request) -> int:
    return effective_user_id(session_from_request(request))


def _resolve_page_lang(request: Request, db: Session) -> str:
    session = session_from_request(request)
    user_id = session.user_id if session else None
    return settings.resolve_ui_lang(db, user_id=user_id)


def render(request: Request, name: str, context: dict, *, status_code: int = 200, db: Session | None = None):
    """TemplateResponse with ui_lang, t/_ helpers, and JS i18n bundle."""
    ctx = dict(context)
    lang = ctx.get("ui_lang")
    if not lang:
        lang = _resolve_page_lang(request, db) if db is not None else settings.DEFAULT_UI_LANG
    t_fn = i18n.make_t(lang)
    ctx["ui_lang"] = lang
    ctx["t"] = t_fn
    ctx["_"] = t_fn
    ctx.setdefault("i18n_js", i18n.js_bundle(lang))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _story_categories(db: Session, user_id: int | None = None) -> dict[str, str]:
    query = db.query(Feed)
    if user_id is not None:
        query = query.filter(Feed.user_id == user_id)
    return {feed.name: feed.category for feed in query.all()}


def _base_context(request: Request, db: Session, active: str) -> dict:
    ingest = snapshot()
    llm = settings.llm_config(db)
    uid = _current_user_id(request)
    session = session_from_request(request)
    lang = settings.resolve_ui_lang(db, user_id=session.user_id if session else None)
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
        "app_github": __github__,
        "homescreen_name": hostname.homescreen_name(db),
        "favicons": favicon.map_for_feeds(db.query(Feed).filter(Feed.user_id == uid).all()),
        "session_role": session.role if session else "user",
        "session_username": session.username if session else "",
        "can_view_status": _session_can_view_status(db, request),
        "reader_setup_nudge": reader_config.needs_setup_nudge(db, uid) if session else False,
        "ui_lang": lang,
    }


@public.get("/login")
def login_page(request: Request, db: Annotated[Session, Depends(get_db)], next: str = "/"):
    nxt = safe_next(next)
    if is_signed_in(request):
        return RedirectResponse(nxt, status_code=303)
    return render(
        request,
        "login.html",
        {
            "request": request,
            "next": nxt,
            "error": None,
            "username": "",
            "using_factory_admin": settings.using_factory_admin(db),
            "app_version": __version__,
            "asset_rev": __asset_rev__,
            "homescreen_name": hostname.homescreen_name(db),
        },
        db=db,
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
    name = username.strip()
    if login_rate_limited(request, name):
        return render(
            request,
            "login.html",
            {
                "request": request,
                "next": nxt,
                "error": "Too many failed attempts. Wait a few minutes and try again.",
                "username": name,
                "using_factory_admin": settings.using_factory_admin(db),
                "app_version": __version__,
                "asset_rev": __asset_rev__,
                "homescreen_name": hostname.homescreen_name(db),
            },
            status_code=429,
            db=db,
        )
    if credentials_match(db, name, password):
        clear_login_failures(request, name)
        identity = resolve_login_identity(db, name)
        response = RedirectResponse(nxt, status_code=303)
        attach_session(
            response,
            identity.username,
            user_id=identity.user_id,
            role=identity.role,
            secure=request_is_https(request, db),
        )
        return response
    record_login_failure(request, name)
    return render(
        request,
        "login.html",
        {
            "request": request,
            "next": nxt,
            "error": "That username or password is not right.",
            "username": name,
            "using_factory_admin": settings.using_factory_admin(db),
            "app_version": __version__,
            "asset_rev": __asset_rev__,
            "homescreen_name": hostname.homescreen_name(db),
        },
        db=db,
    )


@public.get("/login/token/{token}")
def login_via_token(token: str, request: Request, db: Annotated[Session, Depends(get_db)], next: str = "/"):
    nxt = safe_next(next)
    user = users_service.consume_login_token(db, token)
    if user is None:
        return render(
            request,
            "login.html",
            {
                "request": request,
                "next": nxt,
                "error": "That login link is invalid or has expired.",
                "username": "",
                "using_factory_admin": settings.using_factory_admin(db),
                "app_version": __version__,
                "asset_rev": __asset_rev__,
                "homescreen_name": hostname.homescreen_name(db),
            },
            status_code=401,
            db=db,
        )
    response = RedirectResponse(nxt, status_code=303)
    attach_session(
        response,
        user.username,
        user_id=user.id,
        role=user.role,
        secure=request_is_https(request, db),
    )
    return response


@public.api_route("/logout", methods=["GET", "POST"])
def logout_submit():
    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response


@router.get("/")
def briefing_page(request: Request, db: Annotated[Session, Depends(get_db)], day: str = "today"):
    uid = _current_user_id(request)
    briefing_day = normalize_briefing_day(day)
    stories = [story for story in current_stories(db, day=briefing_day, user_id=uid) if not story.saved]
    categories = _story_categories(db, uid)
    icons = favicon.map_for_feeds(db.query(Feed).filter(Feed.user_id == uid).all())
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
    return render(
        request,
        "briefing.html",
        {
            **_base_context(request, db, "briefing"),
            "stories": stories,
            "category_labels": category_labels(db),
            "retention_days": env.story_retention_days,
            "briefing_day": briefing_day,
        },
        db=db,
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
    uid = _current_user_id(request)
    query = (q or "").strip()
    stories = search_stories(db, query, user_id=uid) if query else []
    categories = _story_categories(db, uid)
    icons = favicon.map_for_feeds(db.query(Feed).filter(Feed.user_id == uid).all())
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
    return render(
        request,
        "search.html",
        {
            **_base_context(request, db, "search"),
            "query": query,
            "stories": stories,
        },
        db=db,
    )


@router.get("/saved")
def saved_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    uid = _current_user_id(request)
    items = current_saved_stories(db, user_id=uid)
    icons = favicon.map_for_feeds(db.query(Feed).filter(Feed.user_id == uid).all())
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
    return render(
        request,
        "saved.html",
        {
            **_base_context(request, db, "saved"),
            "items": items,
            "keep_options": saved_articles.KEEP_OPTIONS,
        },
        db=db,
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
    from app.services.translate import FEED_PROVIDER_CHOICES, feed_translate_mode

    uid = _current_user_id(request)
    session = session_from_request(request)
    is_admin = bool(session and session.role == "admin")
    user_row = db.get(User, uid) if uid else None
    can_add_custom = is_admin or bool(user_row and user_row.can_add_custom_sources)
    feeds = (
        db.query(Feed)
        .filter(Feed.user_id == uid)
        .order_by(Feed.enabled.desc(), Feed.name.asc())
        .all()
    )
    now = utcnow()
    for feed in feeds:
        feed.is_muted = feed_is_muted(feed, now)
        feed.health = feed_health(feed, now)
        feed.health_label = feed_health_label(feed, now)
        feed.translate_mode = feed_translate_mode(feed)
    return render(
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
            "translate_modes": FEED_PROVIDER_CHOICES,
            "global_translate_provider": settings.translate_provider(db),
            "can_add_custom_sources": can_add_custom,
        },
        db=db,
    )


@router.get("/catalog")
def catalog_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services.translate import FEED_PROVIDER_CHOICES

    uid = _current_user_id(request)
    session = session_from_request(request)
    is_admin = bool(session and session.role == "admin")
    user_row = db.get(User, uid) if uid else None
    can_add_custom = is_admin or bool(user_row and user_row.can_add_custom_sources)
    return render(
        request,
        "catalog.html",
        {
            **_base_context(request, db, "catalog"),
            "catalog": grouped_catalog(db, user_id=uid, approved_only=not is_admin),
            "custom_feeds": db.query(Feed)
            .filter(Feed.user_id == uid, Feed.catalog_id.is_(None))
            .order_by(Feed.name.asc())
            .all(),
            "category_labels": category_labels(db),
            "translate_modes": FEED_PROVIDER_CHOICES,
            "can_add_custom_sources": can_add_custom,
            "is_admin": is_admin,
        },
        db=db,
    )


@router.get("/status")
def status_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    if not _session_can_view_status(db, request):
        return RedirectResponse("/library", status_code=303)
    uid = _current_user_id(request)
    session = session_from_request(request)
    username = (session.username if session else "") or settings.get_value(db, "admin_username") or "admin"
    story_count = db.query(Story).filter(Story.user_id == uid).count()
    share_url = hostname.get_share_url(db)
    reader = reader_push.snapshot(db, probe=False, user_id=uid)
    delivery = delivery_status(db, user_id=uid)
    public_base = hostname.get_public_base_url(db)
    opds_path = f"/opds/u/{username}"
    x3_path = f"/api/x3/u/{username}"
    return render(
        request,
        "status.html",
        {
            **_base_context(request, db, "status"),
            "story_count": story_count,
            "reader": reader,
            "delivery": delivery,
            "public_base_url": public_base,
            "opds_url": f"{public_base}{opds_path}",
            "opds_path": opds_path,
            "x3_news_url": f"{public_base}{x3_path}/news",
            "x3_path": x3_path,
            "share_url": share_url,
            "lan_url": hostname.get_lan_url(db),
            "qr_svg": qrcode.svg_for(share_url),
            "request_base_url": str(request.base_url).rstrip("/"),
            "instance_name": settings.get_value(db, "instance_name"),
            "x3_catalog_login": settings.catalog_login_enabled(db),
            "x3_catalog_username": settings.catalog_username(db),
            "x3_token_set": bool(
                user_settings_service.get_value(db, uid, "x3_sync_token")
                or settings.get_value(db, "x3_sync_token")
            ),
            "update_check": update.last_check(db),
            "paper": paper_status(db, user_id=uid),
            "reader_device": reader_config.reader_device(db, uid),
            "health": status_health(db),
            "https_enabled": settings.https_enabled(db),
        },
        db=db,
    )


@router.get("/library")
def library_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    uid = _current_user_id(request)
    return render(
        request,
        "library.html",
        {
            **_base_context(request, db, "library"),
            "library_files": _library_items(db, uid),
            "reader": reader_push.snapshot(db, probe=False, user_id=uid),
        },
        db=db,
    )


def _settings_page_context(request: Request, db: Session, tab: str = "device") -> dict:
    session = session_from_request(request)
    role = session.role if session else "user"
    can_ntfy = _session_can_use_ntfy(db, request)
    settings_tab = normalize_settings_tab_for_role(tab, role, can_use_ntfy=can_ntfy)
    uid = _current_user_id(request)
    ntfy.migrate_user_ntfy_from_instance(db, uid)
    user_server = user_settings_service.get_value(db, uid, "ntfy_server").strip()
    household_server = settings.get_value(db, "ntfy_server") or "https://ntfy.sh"
    return {
        **_base_context(request, db, "settings"),
        "settings_tab": settings_tab,
        "settings_tabs": settings_tabs_for(role, can_use_ntfy=can_ntfy),
        "is_admin": role == "admin",
        "can_use_ntfy": can_ntfy,
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
        "x3_token": user_settings_service.secret_hint(db, uid, "x3_sync_token"),
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
        "briefing_category_mix": settings.briefing_category_mix_enabled(db),
        "briefing_category_opds_keys": settings.briefing_category_opds_keys(db),
        "briefing_category_shares": settings.briefing_category_shares(db),
        "briefing_publish_at": briefing_publish_at(db),
        "github_repo": update.repo_from_db(db),
        "update_check": update.last_check(db),
        "categories": list_categories(db),
        "export_categories": list_categories(db),
        "latest_backup": backup.latest_backup(),
        "keyword_include": settings.get_value(db, "keyword_include"),
        "keyword_exclude": settings.get_value(db, "keyword_exclude"),
        "translate_providers": settings.TRANSLATE_PROVIDERS,
        "translate_provider": settings.translate_provider(db),
        "translate_target_languages": translate.TARGET_LANGUAGES,
        "translate_target_lang": settings.translate_target_lang(db),
        **(
            reader_config.settings_context(db, db.get(User, uid))
            if db.get(User, uid) is not None
            else {
                "reader_device": settings.reader_device(db),
                "reader_devices": settings.READER_DEVICES,
                "reader_host": settings.get_value(db, "reader_host"),
                "reader_upload_path": settings.get_value(db, "reader_upload_path"),
                "reader_push_when_online": settings.reader_push_enabled(db),
                "reader_ssh_port": settings.reader_ssh_port(db),
                "reader_ssh_user": settings.reader_ssh_user(db),
                "reader_ssh_password": settings.secret_hint(db, "reader_ssh_password"),
                "reader_setup_nudge": False,
            }
        ),
        "reader_title_pattern": paper_naming.reader_title_pattern(db),
        "reader_category_title_pattern": paper_naming.reader_category_title_pattern(db),
        "reader_date_format": paper_naming.reader_date_format(db),
        "reader_date_formats": paper_naming.DATE_FORMATS,
        "reader_title_tokens": paper_naming.TITLE_TOKENS,
        "reader_category_title_tokens": paper_naming.CATEGORY_TITLE_TOKENS,
        "reader_date_previews": paper_naming.date_format_previews(date.today()),
        "reader_paper_label": settings.get_value(db, "reader_paper_label"),
        "reader_title_preview": paper_naming.paper_display_title(db, date.today()),
        "reader_category_title_preview": paper_naming.paper_category_display_title(db, date.today(), "Tech"),
        "delivery": delivery_status(db, user_id=uid),
        "ntfy_enabled": user_settings_service.flag_enabled(db, uid, "ntfy_enabled"),
        "ntfy_server": user_server or household_server,
        "ntfy_household_server": household_server,
        "ntfy_topic": user_settings_service.get_with_fallback(db, uid, "ntfy_topic"),
        "ntfy_token": user_settings_service.secret_hint(db, uid, "ntfy_token"),
        "ntfy_notify_on_publish": user_settings_service.flag_enabled(db, uid, "ntfy_notify_on_publish"),
        "ntfy_notify_on_push": user_settings_service.flag_enabled(db, uid, "ntfy_notify_on_push"),
        "https_enabled": settings.https_enabled(db),
        "tls_status": tls.certificate_status(db),
        "tls_download_url": f"{str(request.base_url).rstrip('/')}/settings/tls/root-ca.pem",
        "https_share_url": hostname.get_share_url(db) if settings.https_enabled(db) else "",
        "tls_pending_restart": settings.https_enabled(db) and request.url.scheme != "https",
        "ui_lang": settings.resolve_ui_lang(db, user_id=uid),
        "ui_lang_choices": settings.UI_LANG_CHOICES,
        "household_users": users_service.list_users(db),
        "login_qr_user": None,
        "login_qr_svg": None,
        "login_qr_url": None,
        "users_notice": None,
        "approved_catalog_ids": catalog_service.approved_catalog_ids(db),
        "catalog_for_approval": catalog_with_status(db, user_id=uid, approved_only=False),
    }


@router.get("/settings")
def settings_page(request: Request, db: Annotated[Session, Depends(get_db)], tab: str = "device"):
    return render(request, "settings.html", _settings_page_context(request, db, tab), db=db)


@router.post("/library")
def upload_library_file(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str, Form()] = "",
    file: UploadFile = File(...),
):
    data = file.file.read(library.MAX_UPLOAD_BYTES + 1)
    try:
        library.add_library_file(db, file.filename or "document", data, title, user_id=_current_user_id(request))
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
    nxt = _reader_redirect_next(db, request, next, default="/library")
    uid = _current_user_id(request)
    host = reader_push.reader_host(db, user_id=uid)
    online = reader_push.reader_reachable(host, db=db, user_id=uid)
    reader_push.remember_probe(host, online)
    message = f"{host} is {'online' if online else 'asleep'}."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message, "online": online, "host": host})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/push")
def push_reader_now(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = _reader_redirect_next(db, request, next, default="/library")
    uid = _current_user_id(request)
    reader_push.enqueue_briefing_and_library(db, user_id=uid)
    result = reader_push.flush_pending(db, user_id=uid)
    if result.get("online"):
        message = f"Pushed {result.get('uploaded', 0)} file{'s' if result.get('uploaded') != 1 else ''} to the reader."
    else:
        message = "Reader is asleep. Files are queued until it is on Wi-Fi."
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": message, **result})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/queue")
def queue_reader_later(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = _reader_redirect_next(db, request, next, default="/library")
    uid = _current_user_id(request)
    tasks = reader_push.enqueue_briefing_and_library(db, user_id=uid)
    message = f"Queued {len(tasks)} file{'s' if len(tasks) != 1 else ''} for when the reader is on Wi-Fi."
    if not paper_status(db, user_id=uid)["published"]:
        message += " Today's paper is not published yet."
    if _wants_json(request):
        return JSONResponse(
            {"ok": True, "message": message, "pending": len(reader_push.pending_crosspoint(db, user_id=uid))}
        )
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/queue/{task_id}/cancel")
def cancel_reader_queue(
    task_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    next: Annotated[str, Form()] = "/status",
):
    nxt = _reader_redirect_next(db, request, next, default="/library")
    if not reader_push.cancel_pending(db, task_id, user_id=_current_user_id(request)):
        return _form_error(request, "That queued file was already gone.", nxt)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed from the queue."})
    return RedirectResponse(nxt, status_code=303)


@router.post("/reader/publish")
def publish_reader_paper(request: Request, db: Annotated[Session, Depends(get_db)], next: Annotated[str, Form()] = "/status"):
    nxt = _reader_redirect_next(db, request, next, default="/library")
    uid = _current_user_id(request)
    publish_daily_briefing(db, overwrite=True, user_id=uid)
    if reader_config.reader_push_enabled(db, uid):
        reader_push.enqueue_frozen_briefing(db, user_id=uid)
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
    translate: Annotated[str, Form()] = "off",
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
    from app.services.translate import parse_feed_translate_mode

    do_translate, translate_provider = parse_feed_translate_mode(translate)
    uid = _current_user_id(request)
    session = session_from_request(request)
    is_admin = bool(session and session.role == "admin")
    user_row = db.get(User, uid) if uid else None
    if not is_admin and not (user_row and user_row.can_add_custom_sources):
        msg = "Custom sources are not enabled for your account."
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": msg}, status_code=403)
        return _form_error(request, msg, nxt, 403)
    existing = db.query(Feed).filter(Feed.user_id == uid, Feed.url == url.strip()).one_or_none()
    if existing is None:
        db.add(
            Feed(
                user_id=uid,
                name=name.strip() or parsed.netloc,
                url=url.strip(),
                category=category,
                type=kind,
                enabled=True,
                summarize=summarize != "0",
                translate=do_translate,
                translate_provider=translate_provider,
            )
        )
        db.commit()
        added = db.query(Feed).filter(Feed.user_id == uid, Feed.url == url.strip()).one_or_none()
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
    translate: Annotated[str, Form()] = "off",
    keyword_include: Annotated[str, Form()] = "",
    keyword_exclude: Annotated[str, Form()] = "",
):
    feed = db.get(Feed, feed_id)
    if feed is None:
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Feed not found."}, status_code=404)
        return RedirectResponse("/feeds", status_code=303)
    from app.services.translate import parse_feed_translate_mode

    feed.schedule_mode = schedule_mode if schedule_mode in {"global", "custom"} else "global"
    if feed.schedule_mode == "custom":
        try:
            minutes = int(interval_minutes)
            feed.interval_minutes = minutes if minutes > 0 else 60
        except ValueError:
            feed.interval_minutes = 60
    feed.summarize = summarize != "0"
    do_translate, translate_provider = parse_feed_translate_mode(translate)
    feed.translate = do_translate
    feed.translate_provider = translate_provider
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
        if feed.enabled and not favicon.cached_src(feed.favicon_name):
            favicon.capture_for_feed_async(feed.id)
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
    from app.routers.feeds import add_catalog_feed

    session = session_from_request(request)
    is_admin = bool(session and session.role == "admin")
    if not is_admin and not catalog_service.is_catalog_approved(db, catalog_id):
        return _form_error(request, "That source is not approved for this household.", "/catalog", 403)
    add_catalog_feed(db, catalog_id, user_id=_current_user_id(request))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Feed enabled."})
    return RedirectResponse("/catalog", status_code=303)


@router.post("/catalog/{catalog_id}/remove")
def remove_catalog_form(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services.catalog import find_catalog_item

    uid = _current_user_id(request)
    item = find_catalog_item(catalog_id)
    feed = None
    if item:
        feed = (
            db.query(Feed)
            .filter(Feed.user_id == uid)
            .filter((Feed.catalog_id == catalog_id) | (Feed.url == item["url"]))
            .one_or_none()
        )
    else:
        feed = db.query(Feed).filter(Feed.user_id == uid, Feed.catalog_id == catalog_id).one_or_none()
    if feed:
        db.delete(feed)
        db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed the feed."})
    return RedirectResponse("/catalog", status_code=303)


@router.post("/settings")
async def save_settings(
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
    briefing_category_mix: Annotated[str, Form()] = "",
    briefing_publish_at: Annotated[str, Form()] = "",
    github_repo: Annotated[str, Form()] = "",
    keyword_include: Annotated[str, Form()] = "",
    keyword_exclude: Annotated[str, Form()] = "",
    translate_provider: Annotated[str, Form()] = "google",
    translate_target_lang: Annotated[str, Form()] = "en",
    reader_host: Annotated[str, Form()] = "",
    reader_upload_path: Annotated[str, Form()] = "",
    reader_push_when_online: Annotated[str, Form()] = "",
    reader_device: Annotated[str, Form()] = "xteink",
    reader_ssh_port: Annotated[str, Form()] = "",
    reader_ssh_user: Annotated[str, Form()] = "",
    reader_ssh_password: Annotated[str, Form()] = "",
    clear_reader_ssh_password: Annotated[str, Form()] = "",
    reader_title_pattern: Annotated[str, Form()] = "",
    reader_category_title_pattern: Annotated[str, Form()] = "",
    reader_date_format: Annotated[str, Form()] = "iso",
    reader_paper_label: Annotated[str, Form()] = "",
    ntfy_enabled: Annotated[str, Form()] = "",
    ntfy_server: Annotated[str, Form()] = "",
    ntfy_topic: Annotated[str, Form()] = "",
    ntfy_token: Annotated[str, Form()] = "",
    clear_ntfy_token: Annotated[str, Form()] = "",
    ntfy_notify_on_publish: Annotated[str, Form()] = "",
    ntfy_notify_on_push: Annotated[str, Form()] = "",
    settings_tab: Annotated[str, Form()] = "device",
):
    session = session_from_request(request)
    role = session.role if session else "user"
    is_admin = role == "admin"
    can_ntfy = _session_can_use_ntfy(db, request)
    tab = normalize_settings_tab_for_role(settings_tab, role, can_use_ntfy=can_ntfy)
    if tab in ADMIN_ONLY_SETTINGS_TABS and not is_admin:
        return _settings_error(request, "That settings section is for the household admin.", "device")
    if tab == "notifications" and not can_ntfy:
        return _settings_error(request, "Phone alerts are not enabled for your account.", "device")
    form = await request.form()
    reauth = False
    current_user, current_pass = settings.get_admin_credentials(db)
    changing_password = bool(new_password.strip())
    changing_username = bool(admin_username.strip()) and admin_username.strip() != current_user

    if (changing_password or changing_username) and not is_admin:
        return _settings_error(request, "Only the household admin can change login credentials here.", tab)

    if changing_password or changing_username:
        if not passwords.verify_password(current_pass, current_password):
            return _settings_error(request, "Current password is incorrect.", tab)
        if changing_password:
            if new_password != new_password_confirm:
                return _settings_error(request, "New passwords do not match.", tab)
            if len(new_password) < 4:
                return _settings_error(request, "New password must be at least 4 characters.", tab)
            hashed = passwords.hash_password(new_password)
            settings.set_value(db, "admin_password", hashed)
            admin_row = db.query(User).filter(User.username == current_user).one_or_none()
            if admin_row is not None:
                admin_row.password = hashed
                db.commit()
            reauth = True
        if changing_username:
            settings.set_value(db, "admin_username", admin_username.strip())
            admin_row = db.query(User).filter(User.username == current_user).one_or_none()
            if admin_row is not None:
                admin_row.username = admin_username.strip()
                db.commit()
            reauth = True

    turning_https_on = False
    turning_https_off = False
    hostname_cert_renewed = False
    if is_admin:
        previous_https = settings.https_enabled(db)
        want_https = bool(str(form.get("https_enabled") or "").strip())
        turning_https_on = want_https and not previous_https
        turning_https_off = previous_https and not want_https
        if want_https:
            try:
                tls.ensure_certificate(db)
            except Exception:
                logger.exception("TLS certificate generation failed")
                return _settings_error(
                    request,
                    "Could not create the local HTTPS certificate. Check disk space and try again.",
                    tab,
                )
        settings.set_value(db, "https_enabled", "1" if want_https else "0")
    else:
        want_https = settings.https_enabled(db)
    ui_lang = str(form.get("ui_lang") or settings.DEFAULT_UI_LANG).strip().lower()
    allowed_langs = {code for code, _label in settings.UI_LANG_CHOICES}
    if ui_lang not in allowed_langs:
        ui_lang = settings.DEFAULT_UI_LANG
    if is_admin:
        settings.set_value(db, "ui_lang", ui_lang)
    session = session_from_request(request)
    if session and session.user_id:
        user_settings_service.set_value(db, session.user_id, "ui_lang", ui_lang)
        admin_row = db.get(User, session.user_id)
        if admin_row is not None:
            admin_row.ui_lang = ui_lang
            db.commit()

    if is_admin:
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

        settings.set_value(db, "x3_catalog_login", "1" if x3_catalog_login else "0")
        if x3_catalog_username.strip():
            settings.set_value(db, "x3_catalog_username", x3_catalog_username.strip()[:80])
        else:
            settings.clear_value(db, "x3_catalog_username")
        settings.set_value(db, "x3_device_id", x3_device_id.strip())

    settings.set_value(db, "keyword_include", keyword_include.strip())
    settings.set_value(db, "keyword_exclude", keyword_exclude.strip())
    if is_admin:
        provider = translate_provider.strip().lower()
        if provider in settings.TRANSLATE_PROVIDER_IDS:
            settings.set_value(db, "translate_provider", provider)
    settings.set_value(db, "translate_target_lang", translate.normalize_target_lang(translate_target_lang))
    uid = _current_user_id(request)
    user_row = db.get(User, uid)
    if user_row is not None:
        try:
            reader_config.save_reader_settings(
                db,
                user_row,
                reader_device_value=reader_device,
                reader_host_value=reader_host,
                reader_upload_path_value=reader_upload_path,
                reader_push_when_online=bool(reader_push_when_online),
                reader_ssh_port_value=reader_ssh_port,
                reader_ssh_user_value=reader_ssh_user,
                reader_ssh_password_value=reader_ssh_password,
                clear_reader_ssh_password=bool(clear_reader_ssh_password),
            )
        except ValueError as exc:
            return _settings_error(request, str(exc), tab)
    if is_admin:
        settings.set_value(db, "reader_title_pattern", paper_naming.normalize_title_pattern(reader_title_pattern))
        settings.set_value(
            db,
            "reader_category_title_pattern",
            paper_naming.normalize_category_title_pattern(reader_category_title_pattern),
        )
        settings.set_value(db, "reader_date_format", paper_naming.normalize_date_format(reader_date_format))
        label = paper_naming.normalize_paper_label(reader_paper_label)
        if label:
            settings.set_value(db, "reader_paper_label", label)
        else:
            settings.clear_value(db, "reader_paper_label")

    # Per-user ntfy: only when admin grants can_use_ntfy (admins always allowed).
    if can_ntfy:
        ntfy.migrate_user_ntfy_from_instance(db, uid)
        user_settings_service.set_value(db, uid, "ntfy_enabled", "1" if ntfy_enabled else "0")
        topic = ntfy_topic.strip()
        if topic:
            user_settings_service.set_value(db, uid, "ntfy_topic", topic)
        else:
            user_settings_service.clear_value(db, uid, "ntfy_topic")
        if clear_ntfy_token:
            user_settings_service.clear_value(db, uid, "ntfy_token")
        elif ntfy_token.strip():
            user_settings_service.set_value(db, uid, "ntfy_token", ntfy_token.strip())
        user_settings_service.set_value(db, uid, "ntfy_notify_on_publish", "1" if ntfy_notify_on_publish else "0")
        user_settings_service.set_value(db, uid, "ntfy_notify_on_push", "1" if ntfy_notify_on_push else "0")
        server_raw = ntfy_server.strip()
        if is_admin:
            # Admin sets the household default server on instance settings.
            settings.set_value(db, "ntfy_server", ntfy.normalize_server(server_raw))
            user_settings_service.clear_value(db, uid, "ntfy_server")
        else:
            if server_raw and ntfy.normalize_server(server_raw) != ntfy.normalize_server(
                settings.get_value(db, "ntfy_server")
            ):
                user_settings_service.set_value(db, uid, "ntfy_server", ntfy.normalize_server(server_raw))
            else:
                user_settings_service.clear_value(db, uid, "ntfy_server")

    if clear_x3_sync_token:
        user_settings_service.clear_value(db, uid, "x3_sync_token")
        if is_admin:
            settings.clear_value(db, "x3_sync_token")
    elif x3_sync_token.strip():
        user_settings_service.set_value(db, uid, "x3_sync_token", x3_sync_token.strip())
        if is_admin:
            settings.set_value(db, "x3_sync_token", x3_sync_token.strip())

    if is_admin:
        wanted_host = hostname.normalize_hostname(device_hostname)
        previous_host = hostname.normalize_hostname(settings.get_value(db, "device_hostname"))
        hostname_changed = wanted_host != previous_host
        if wanted_host:
            if not hostname.valid_hostname(wanted_host):
                return _settings_error(request, "Hostname must be letters, digits, or hyphens.", tab)
            settings.set_value(db, "device_hostname", wanted_host)
            hostname.apply_os_hostname(wanted_host)
        else:
            settings.clear_value(db, "device_hostname")
        if want_https:
            try:
                tls.ensure_certificate(db)
            except Exception:
                logger.exception("TLS certificate refresh after hostname save failed")
                return _settings_error(
                    request,
                    "HTTPS is on but the certificate could not be refreshed for this hostname.",
                    tab,
                )
            if hostname_changed:
                hostname_cert_renewed = True
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
    settings.set_value(db, "briefing_category_mix", "1" if briefing_category_mix else "0")
    category_shares: dict[str, int] = {}
    opds_keys: set[str] = set()
    for category in list_categories(db):
        raw = str(form.get(f"category_share_{category.key}", "") or "").strip()
        if raw == "":
            continue
        try:
            percent = int(raw)
        except ValueError:
            return _settings_error(request, f"Category share for {category.label} must be a whole number.", tab)
        if percent < 0 or percent > 100:
            return _settings_error(request, "Category shares must be between 0 and 100.", tab)
        category_shares[category.key] = percent
    explicit_total = sum(value for value in category_shares.values() if value > 0)
    if explicit_total > 100:
        return _settings_error(request, "Category percentages cannot add up to more than 100.", tab)
    settings.set_value(db, "briefing_category_shares", settings.encode_category_shares(category_shares))
    if tab == "publication":
        for category in list_categories(db):
            if form.get(f"category_opds_{category.key}"):
                opds_keys.add(category.key)
        settings.set_value(db, "briefing_category_opds_keys", settings.encode_category_opds_keys(opds_keys))
    if is_admin and briefing_publish_at.strip():
        settings.set_value(db, "briefing_publish_at", normalize_publish_at(briefing_publish_at))
    if is_admin:
        repo = update.normalize_repo(github_repo)
        if github_repo.strip() and not repo:
            return _settings_error(request, "GitHub repository must look like owner/NewsCast.", tab)
        if repo:
            settings.set_value(db, "github_repo", repo)
        else:
            settings.clear_value(db, "github_repo")

    message = "Settings saved."
    if is_admin and turning_https_off:
        update.schedule_restart()
        message = "HTTPS is off. NewsCast is restarting on plain HTTP."
    elif is_admin and turning_https_on:
        message = (
            "Certificate ready. Download the root CA below, trust it on each device, "
            "then use Restart to enable HTTPS."
        )
    elif is_admin and hostname_cert_renewed and want_https:
        message = (
            "Settings saved. The certificate was renewed for this hostname — "
            "restart NewsCast when you are ready."
        )

    payload = {"ok": True, "message": message, "reauth": reauth}
    if _wants_json(request):
        response: JSONResponse | RedirectResponse = JSONResponse(payload)
    elif reauth:
        response = RedirectResponse("/login", status_code=303)
    else:
        response = RedirectResponse(settings_path(tab), status_code=303)
    if reauth:
        clear_session(response)
    return response


@router.post("/settings/tls/restart")
def restart_for_tls(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can restart NewsCast.", settings_path("device"), 403)
    if not settings.https_enabled(db):
        return _form_error(request, "Turn on Use HTTPS on the LAN first.", settings_path("device"))
    if not tls.CA_CERT.exists():
        return _form_error(
            request,
            "No certificate yet. Save General settings with HTTPS enabled first.",
            settings_path("device"),
        )
    update.schedule_restart()
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "message": f"Restarting… then open {hostname.get_share_url(db)} (not http://).",
            }
        )
    return RedirectResponse(settings_path("device"), status_code=303)


@router.get("/settings/tls/root-ca.pem", name="download_root_ca")
def download_root_ca(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can download the root CA.", settings_path("device"), 403)
    try:
        pem = tls.root_ca_pem_bytes()
    except FileNotFoundError:
        return _form_error(
            request,
            "No root CA yet. Turn on Use HTTPS on the LAN and save settings first.",
            settings_path("device"),
            404,
        )
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="newscast-root-ca.pem"'},
    )


@router.post("/settings/updates/check")
def check_updates(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only the household admin can check for updates.", settings_path("device"))
    result = update.check_latest(db)
    if _wants_json(request):
        return JSONResponse({"ok": bool(result.get("ok")), "message": result.get("message") or "Checked GitHub."})
    return RedirectResponse(settings_path("update"), status_code=303)


@router.post("/settings/updates/install")
def install_update(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only the household admin can install updates.", settings_path("device"))
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
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only the household admin can roll back updates.", settings_path("device"))
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
def download_backup(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can download backups.", settings_path("backup"), 403)
    path = backup.write_backup()
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/settings/backup/restore")
def restore_backup_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can restore backups.", settings_path("backup"), 403)
    data = file.file.read()
    try:
        backup.restore_backup(data)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("backup"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Backup restored. Settings and sources are back."})
    return RedirectResponse(settings_path("backup"), status_code=303)


@router.post("/settings/reader/nudge-dismiss")
def dismiss_reader_setup_nudge(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    next: Annotated[str, Form()] = "/",
):
    session = session_from_request(request)
    if not session or not session.user_id:
        return RedirectResponse("/login", status_code=303)
    reader_config.clear_setup_nudge(db, session.user_id)
    nxt = safe_next(next)
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return RedirectResponse(nxt, status_code=303)


@router.post("/settings/users")
def create_user_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    can_add_custom_sources: Annotated[str, Form()] = "",
    can_use_ntfy: Annotated[str, Form()] = "",
    can_view_status: Annotated[str, Form()] = "",
    copy_admin_reader: Annotated[str, Form()] = "",
):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can create users.", settings_path("users"), 403)
    try:
        user = users_service.create_user(
            db,
            username=username,
            password=password,
            role="user",
            can_add_custom_sources=bool(can_add_custom_sources),
            can_use_ntfy=bool(can_use_ntfy),
            can_view_status=bool(can_view_status),
        )
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("users"))
    if copy_admin_reader:
        reader_config.copy_admin_reader_settings(db, user)
    token = users_service.issue_login_token(db, user)
    share = hostname.get_public_base_url(db).rstrip("/")
    login_url = f"{share}/login/token/{token}"
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "User created.", "login_url": login_url})
    # Re-render settings users tab with QR so admin can scan once.
    return render(
        request,
        "settings.html",
        {
            **_settings_page_context(request, db, "users"),
            "login_qr_user": user.username,
            "login_qr_svg": qrcode.svg_for(login_url),
            "login_qr_url": login_url,
            "users_notice": f"Created {user.username}. Scan the QR so they can sign in once.",
        },
        db=db,
    )


@router.post("/settings/users/{user_id}/permissions")
@router.post("/settings/users/{user_id}")
def update_user_form(
    user_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    can_add_custom_sources: Annotated[str, Form()] = "",
    can_use_ntfy: Annotated[str, Form()] = "",
    can_view_status: Annotated[str, Form()] = "",
    active: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    new_password_confirm: Annotated[str, Form()] = "",
):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can change user settings.", settings_path("users"), 403)
    user = db.get(User, user_id)
    if user is None:
        return _form_error(request, "User not found.", settings_path("users"), 404)

    password = new_password.strip()
    if password or new_password_confirm.strip():
        if password != new_password_confirm.strip():
            return _form_error(request, "New passwords do not match.", settings_path("users"))

    try:
        users_service.update_user(
            db,
            user,
            can_add_custom_sources=bool(can_add_custom_sources) if user.role != "admin" else True,
            can_use_ntfy=bool(can_use_ntfy) if user.role != "admin" else True,
            can_view_status=bool(can_view_status) if user.role != "admin" else True,
            active=bool(active) if user.role != "admin" else True,
            new_password=password or None,
        )
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("users"))

    if _wants_json(request):
        return JSONResponse({"ok": True, "message": f"Updated {user.username}."})
    return render(
        request,
        "settings.html",
        {
            **_settings_page_context(request, db, "users"),
            "users_notice": f"Saved settings for {user.username}.",
        },
        db=db,
    )


@router.post("/settings/users/{user_id}/delete")
def delete_user_form(user_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can remove users.", settings_path("users"), 403)
    user = db.get(User, user_id)
    if user is None:
        return _form_error(request, "User not found.", settings_path("users"), 404)
    try:
        username = users_service.delete_user(db, user, actor_id=session.user_id)
    except ValueError as exc:
        return _form_error(request, str(exc), settings_path("users"))
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": f"Removed {username}."})
    return render(
        request,
        "settings.html",
        {
            **_settings_page_context(request, db, "users"),
            "users_notice": f"Removed {username}.",
        },
        db=db,
    )


@router.post("/settings/users/{user_id}/qr")
def show_user_login_qr(user_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can issue login QR codes.", settings_path("users"), 403)
    user = db.get(User, user_id)
    if user is None or not user.active:
        return _form_error(request, "User not found.", settings_path("users"), 404)
    token = users_service.issue_login_token(db, user)
    share = hostname.get_public_base_url(db).rstrip("/")
    login_url = f"{share}/login/token/{token}"
    return render(
        request,
        "settings.html",
        {
            **_settings_page_context(request, db, "users"),
            "login_qr_user": user.username,
            "login_qr_svg": qrcode.svg_for(login_url),
            "login_qr_url": login_url,
        },
        db=db,
    )


@router.post("/settings/catalog/approvals")
async def save_catalog_approvals(request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    if not session or session.role != "admin":
        return _form_error(request, "Only admins can approve catalog sources.", settings_path("catalog"), 403)
    form = await request.form()
    approved = {str(key)[len("approve_") :] for key in form.keys() if str(key).startswith("approve_")}
    catalog_service.set_catalog_approvals(db, approved)
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Catalog approvals saved."})
    return RedirectResponse(settings_path("catalog"), status_code=303)


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


def _library_items(db: Session, user_id: int | None = None) -> list[dict]:
    query = db.query(LibraryFile)
    if user_id is not None:
        query = query.filter(LibraryFile.user_id == user_id)
    items = query.order_by(LibraryFile.created_at.desc()).all()
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
