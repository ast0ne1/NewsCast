from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
BRIEFING_DIR = DATA_DIR / "briefings"
LIBRARY_DIR = DATA_DIR / "library"
FAVICON_DIR = DATA_DIR / "favicons"
BUNDLED_FAVICON_DIR = ROOT_DIR / "app" / "data" / "favicons"
PACKAGES_DIR = DATA_DIR / "packages"
BACKUPS_DIR = DATA_DIR / "backups"
UPDATES_DIR = DATA_DIR / "updates"
TLS_DIR = DATA_DIR / "tls"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///./data/newscast.db"
    public_base_url: str = "http://127.0.0.1:8000"
    device_hostname: str = ""

    admin_username: str = "admin"
    admin_password: str = "admin"
    session_secret: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_provider: str = "openai"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    instance_name: str = ""

    ingest_interval_minutes: int = 60
    briefing_limit: int = 20
    max_stories_per_feed: int = 8
    story_retention_days: int = 7
    seed_recommended_feeds: bool = True
    github_repo: str = ""

    x3_sync_token: str = ""
    x3_catalog_login: str = ""
    x3_catalog_username: str = ""
    x3_device_id: str = ""
    x3_briefing_format: str = "txt"
    x3_save_path: str = "/Pushed Files/NewsCast/"


env = EnvSettings()
