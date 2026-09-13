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
    "x3_sync_token",
    "x3_catalog_login",
    "x3_catalog_username",
    "x3_device_id",
    "x3_briefing_format",
    "ingest_interval_minutes",
    "briefing_limit",
    "device_hostname",
    "github_repo",
    "keyword_include",
    "keyword_exclude",
    "reader_host",
    "reader_upload_path",
    "reader_push_when_online",
)


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
    if key == "reader_host":
        return "crosspoint.local"
    if key == "reader_upload_path":
        return "/News"
    if key == "reader_push_when_online":
        return "0"
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


def catalog_username(db: Session) -> str:
    return get_value(db, "x3_catalog_username").strip() or "newscast"


def briefing_limit(db: Session) -> int:
    value = get_int(db, "briefing_limit", DEFAULT_BRIEFING_LIMIT)
    return value if value in BRIEFING_LIMIT_VALUES else DEFAULT_BRIEFING_LIMIT


def get_int(db: Session, key: str, fallback: int) -> int:
    raw = get_value(db, key)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def get_admin_credentials(db: Session) -> tuple[str, str]:
    return get_value(db, "admin_username"), get_value(db, "admin_password")


def using_factory_admin(db: Session) -> bool:
    username, password = get_admin_credentials(db)
    return username == DEFAULT_ADMIN_USERNAME and password == DEFAULT_ADMIN_PASSWORD


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
