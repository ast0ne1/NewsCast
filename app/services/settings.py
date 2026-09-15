import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import env
from app.models import Setting

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"

OPENAI_MODELS = [
    ("gpt-4o-mini", "gpt-4o-mini — fast and inexpensive"),
    ("gpt-4o", "gpt-4o — stronger summaries"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4.1", "gpt-4.1"),
    ("o4-mini", "o4-mini"),
]
OPENAI_MODEL_IDS = {item[0] for item in OPENAI_MODELS}

LLM_PROVIDERS = [
    ("openai", "OpenAI — default for the Pi"),
    ("ollama", "Ollama — local model"),
]
LLM_PROVIDER_IDS = {item[0] for item in LLM_PROVIDERS}

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    api_key: str
    base_url: str | None
    ready: bool
    label: str

REFRESH_INTERVALS = [
    (15, "Every 15 minutes"),
    (30, "Every 30 minutes"),
    (60, "Every hour"),
    (120, "Every 2 hours"),
    (360, "Every 6 hours"),
    (720, "Every 12 hours"),
    (1440, "Once a day"),
]
BRIEFING_LIMITS = [
    (10, "10 stories"),
    (20, "20 stories"),
    (30, "30 stories"),
    (40, "40 stories"),
    (50, "50 stories"),
]
BRIEFING_LIMIT_VALUES = {value for value, _label in BRIEFING_LIMITS}
DEFAULT_BRIEFING_LIMIT = 20
IMPORTANCE_MIN_CHOICES = [
    (1, "1+ — Include all scored"),
    (2, "2+ — Interesting and above"),
    (3, "3+ — Worth reading and above"),
    (4, "4+ — Important and major only"),
    (5, "5 — Major stories only"),
]
IMPORTANCE_MIN_VALUES = {value for value, _label in IMPORTANCE_MIN_CHOICES}
DEFAULT_MIN_IMPORTANCE = 3
TRANSLATE_PROVIDERS = [
    ("google", "Google Translate (Chrome fallback)"),
    ("llm", "Configured LLM (OpenAI or Ollama)"),
]
TRANSLATE_PROVIDER_IDS = {value for value, _label in TRANSLATE_PROVIDERS}
DEFAULT_TRANSLATE_PROVIDER = "google"
DEFAULT_TRANSLATE_TARGET_LANG = "en"
READER_DEVICES = [
    ("xteink", "Xteink — CrossPoint"),
    ("kobo", "Kobo — KOReader"),
]
READER_DEVICE_IDS = {value for value, _label in READER_DEVICES}
DEFAULT_READER_DEVICE = "xteink"
DEFAULT_XTEINK_HOST = "crosspoint.local"
DEFAULT_XTEINK_FOLDER = "/News"
DEFAULT_KOBO_FOLDER = "/mnt/onboard/News"
DEFAULT_KOBO_SSH_PORT = 2222
DEFAULT_KOBO_SSH_USER = "root"
DEFAULT_READER_TITLE_PATTERN = "NewsCast - {hostname} {instance} {date}"
DEFAULT_READER_DATE_FORMAT = "iso"


def format_interval_short(minutes: int | None) -> str:
    value = int(minutes or 0)
    if value >= 60:
        hours = value / 60
        if value % 60 == 0:
            whole = value // 60
            return "1 hr" if whole == 1 else f"{whole} hrs"
        shown = f"{hours:.1f}".rstrip("0").rstrip(".")
        return f"{shown} hrs"
    return f"{value} min"

UI_KEYS = (
    "admin_username",
    "admin_password",
    "openai_api_key",
    "openai_model",
    "llm_provider",
    "ollama_base_url",
    "ollama_model",
    "instance_name",
    "https_enabled",
    "ui_lang",
    "x3_sync_token",
    "x3_catalog_login",
    "x3_catalog_username",
    "x3_device_id",
    "x3_briefing_format",
    "ingest_interval_minutes",
    "ingest_active_start",
    "ingest_active_end",
    "briefing_limit",
    "briefing_min_importance",
    "briefing_category_mix",
    "briefing_category_shares",
    "briefing_category_opds_keys",
    "briefing_publish_at",
    "device_hostname",
    "github_repo",
    "keyword_include",
    "keyword_exclude",
    "translate_provider",
    "translate_target_lang",
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
    "ntfy_enabled",
    "ntfy_server",
    "ntfy_topic",
    "ntfy_token",
    "ntfy_notify_on_publish",
    "ntfy_notify_on_push",
    "ntfy_last_publish_notified_day",
    "ntfy_last_push_notified_day",
)

UI_LANG_CHOICES = [
    ("en", "English"),
    ("es", "Spanish"),
]
DEFAULT_UI_LANG = "en"


def _env_value(key: str) -> str:
    mapping = {
        "admin_username": env.admin_username,
        "admin_password": env.admin_password,
        "openai_api_key": env.openai_api_key,
        "openai_model": env.openai_model,
        "llm_provider": env.llm_provider,
        "ollama_base_url": env.ollama_base_url,
        "ollama_model": env.ollama_model,
        "instance_name": env.instance_name,
        "x3_sync_token": env.x3_sync_token,
        "x3_catalog_login": env.x3_catalog_login,
        "x3_catalog_username": env.x3_catalog_username,
        "x3_device_id": env.x3_device_id,
        "x3_briefing_format": env.x3_briefing_format,
        "ingest_interval_minutes": str(env.ingest_interval_minutes),
        "briefing_limit": str(env.briefing_limit),
        "device_hostname": env.device_hostname,
        "github_repo": env.github_repo,
    }
    return mapping.get(key, "") or ""


def _default_value(key: str) -> str:
    if key == "admin_username":
        return DEFAULT_ADMIN_USERNAME
    if key == "admin_password":
        return DEFAULT_ADMIN_PASSWORD
    if key == "openai_model":
        return "gpt-4o-mini"
    if key == "llm_provider":
        return "openai"
    if key == "ollama_base_url":
        return DEFAULT_OLLAMA_URL
    if key == "x3_briefing_format":
        return "txt"
    if key == "x3_catalog_username":
        return "newscast"
    if key == "ingest_interval_minutes":
        return "60"
    if key == "briefing_limit":
        return str(DEFAULT_BRIEFING_LIMIT)
    if key == "briefing_min_importance":
        return str(DEFAULT_MIN_IMPORTANCE)
    if key == "translate_provider":
        return DEFAULT_TRANSLATE_PROVIDER
    if key == "translate_target_lang":
        return DEFAULT_TRANSLATE_TARGET_LANG
    if key == "briefing_publish_at":
        return "06:30"
    if key == "reader_device":
        return DEFAULT_READER_DEVICE
    if key == "reader_push_when_online":
        return "0"
    if key == "reader_ssh_port":
        return str(DEFAULT_KOBO_SSH_PORT)
    if key == "reader_ssh_user":
        return DEFAULT_KOBO_SSH_USER
    if key == "reader_title_pattern":
        return DEFAULT_READER_TITLE_PATTERN
    if key == "reader_category_title_pattern":
        return "NewsCast - {hostname} {instance} {category} {date}"
    if key == "reader_date_format":
        return DEFAULT_READER_DATE_FORMAT
    if key == "ntfy_server":
        return "https://ntfy.sh"
    if key in {
        "ntfy_enabled",
        "ntfy_notify_on_publish",
        "ntfy_notify_on_push",
        "https_enabled",
    }:
        return "0"
    if key == "ui_lang":
        return DEFAULT_UI_LANG
    return ""


def get_setting_row(db: Session, key: str) -> Setting | None:
    return db.get(Setting, key)


def get_value(db: Session, key: str) -> str:
    row = get_setting_row(db, key)
    if row is not None and row.value != "":
        return row.value
    env_val = _env_value(key)
    if env_val:
        return env_val
    return _default_value(key)


def get_source(db: Session, key: str) -> str:
    row = get_setting_row(db, key)
    if row is not None and row.value != "":
        return "ui"
    if _env_value(key):
        return "env"
    if _default_value(key):
        return "default"
    return "unset"


def set_value(db: Session, key: str, value: str) -> None:
    row = get_setting_row(db, key)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(Setting(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    db.commit()


def clear_value(db: Session, key: str) -> None:
    row = get_setting_row(db, key)
    if row is None:
        return
    db.delete(row)
    db.commit()


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("sk-") and len(value) > 8:
        return f"sk-…{value[-4:]}"
    if len(value) <= 4:
        return "••••"
    return f"…{value[-4:]}"


def secret_hint(db: Session, key: str) -> dict:
    value = get_value(db, key)
    source = get_source(db, key)
    return {
        "set": bool(value),
        "source": source,
        "hint": mask_secret(value) if value else "",
    }


def flag_enabled(db: Session, key: str) -> bool:
    return get_value(db, key).strip().lower() in {"1", "true", "on", "yes"}


def catalog_login_enabled(db: Session) -> bool:
    return flag_enabled(db, "x3_catalog_login")


def reader_push_enabled(db: Session) -> bool:
    return flag_enabled(db, "reader_push_when_online")


def normalize_reader_device(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in READER_DEVICE_IDS else DEFAULT_READER_DEVICE


def reader_device(db: Session) -> str:
    return normalize_reader_device(get_value(db, "reader_device"))


def reader_is_kobo(db: Session) -> bool:
    return reader_device(db) == "kobo"


def reader_ssh_port(db: Session) -> int:
    value = get_int(db, "reader_ssh_port", DEFAULT_KOBO_SSH_PORT)
    return value if 1 <= value <= 65535 else DEFAULT_KOBO_SSH_PORT


def reader_ssh_user(db: Session) -> str:
    return get_value(db, "reader_ssh_user").strip() or DEFAULT_KOBO_SSH_USER


def catalog_username(db: Session) -> str:
    return get_value(db, "x3_catalog_username").strip() or "newscast"


def briefing_limit(db: Session) -> int:
    value = get_int(db, "briefing_limit", DEFAULT_BRIEFING_LIMIT)
    return value if value in BRIEFING_LIMIT_VALUES else DEFAULT_BRIEFING_LIMIT


def briefing_min_importance(db: Session) -> int:
    value = get_int(db, "briefing_min_importance", DEFAULT_MIN_IMPORTANCE)
    return value if value in IMPORTANCE_MIN_VALUES else DEFAULT_MIN_IMPORTANCE


def briefing_category_mix_enabled(db: Session) -> bool:
    return flag_enabled(db, "briefing_category_mix")


def parse_category_opds_keys(raw: str | None) -> set[str]:
    text = (raw or "").strip()
    if not text:
        return set()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return set()
    if isinstance(payload, dict):
        items = [key for key, value in payload.items() if value]
    elif isinstance(payload, list):
        items = payload
    else:
        return set()
    keys: set[str] = set()
    for item in items:
        slug = str(item or "").strip().lower()
        if slug:
            keys.add(slug)
    return keys


def encode_category_opds_keys(keys: set[str] | list[str]) -> str:
    cleaned = sorted({str(key).strip().lower() for key in keys if str(key or "").strip()})
    return json.dumps(cleaned, separators=(",", ":"))


def briefing_category_opds_keys(db: Session) -> set[str]:
    return parse_category_opds_keys(get_value(db, "briefing_category_opds_keys"))


def category_opds_enabled(db: Session, category_key: str) -> bool:
    slug = (category_key or "").strip().lower()
    return bool(slug) and slug in briefing_category_opds_keys(db)


def briefing_category_opds_enabled(db: Session) -> bool:
    """True when at least one category is opted into OPDS papers."""
    return bool(briefing_category_opds_keys(db))


def parse_category_shares(raw: str | None) -> dict[str, int]:
    """Return explicit category percentages. Missing keys share the leftover; 0 excludes."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    shares: dict[str, int] = {}
    for key, value in payload.items():
        slug = str(key or "").strip().lower()
        if not slug:
            continue
        try:
            percent = int(value)
        except (TypeError, ValueError):
            continue
        if percent < 0:
            continue
        shares[slug] = min(percent, 100)
    return shares


def briefing_category_shares(db: Session) -> dict[str, int]:
    return parse_category_shares(get_value(db, "briefing_category_shares"))


def encode_category_shares(shares: dict[str, int]) -> str:
    cleaned = {
        str(key).strip().lower(): min(max(int(value), 0), 100)
        for key, value in shares.items()
        if str(key or "").strip()
    }
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def translate_provider(db: Session) -> str:
    value = get_value(db, "translate_provider").strip().lower()
    return value if value in TRANSLATE_PROVIDER_IDS else DEFAULT_TRANSLATE_PROVIDER


def translate_target_lang(db: Session) -> str:
    from app.services.translate import normalize_target_lang

    return normalize_target_lang(get_value(db, "translate_target_lang"))


def get_int(db: Session, key: str, fallback: int) -> int:
    raw = get_value(db, key)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def get_admin_credentials(db: Session) -> tuple[str, str]:
    return get_value(db, "admin_username"), get_value(db, "admin_password")


def https_enabled(db: Session) -> bool:
    return flag_enabled(db, "https_enabled")


def ui_lang(db: Session) -> str:
    value = (get_value(db, "ui_lang") or DEFAULT_UI_LANG).strip().lower()
    allowed = {code for code, _label in UI_LANG_CHOICES}
    return value if value in allowed else DEFAULT_UI_LANG


def resolve_ui_lang(db: Session, user_id: int | None = None) -> str:
    """Prefer per-user ui_lang when set; otherwise the instance General value."""
    allowed = {code for code, _label in UI_LANG_CHOICES}
    if user_id is not None:
        from app.models import User
        from app.services import user_settings as user_settings_service

        raw = user_settings_service.get_value(db, user_id, "ui_lang", default="").strip().lower()
        if raw in allowed:
            return raw
        user = db.get(User, user_id)
        if user is not None and user.ui_lang:
            column = user.ui_lang.strip().lower()
            if column in allowed:
                return column
    return ui_lang(db)


def using_factory_admin(db: Session) -> bool:
    from app.services import passwords

    username, password = get_admin_credentials(db)
    if username != DEFAULT_ADMIN_USERNAME:
        return False
    return passwords.verify_password(password, DEFAULT_ADMIN_PASSWORD)


def normalize_provider(value: str) -> str:
    provider = (value or "").strip().lower()
    return provider if provider in LLM_PROVIDER_IDS else "openai"


def normalize_ollama_root(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    return raw or DEFAULT_OLLAMA_URL


def llm_config(db: Session) -> LlmConfig:
    provider = normalize_provider(get_value(db, "llm_provider"))
    if provider == "ollama":
        model = get_value(db, "ollama_model").strip()
        root = normalize_ollama_root(get_value(db, "ollama_base_url"))
        return LlmConfig(
            provider="ollama",
            model=model,
            api_key="ollama",
            base_url=f"{root}/v1",
            ready=bool(model),
            label=f"Ollama {model}" if model else "Ollama",
        )
    model = get_value(db, "openai_model").strip() or "gpt-4o-mini"
    api_key = get_value(db, "openai_api_key").strip()
    return LlmConfig(
        provider="openai",
        model=model,
        api_key=api_key,
        base_url=None,
        ready=bool(api_key),
        label=f"OpenAI {model}" if api_key else "OpenAI",
    )
