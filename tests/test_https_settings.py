from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services import hostname, settings


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_https_off_keeps_http_urls(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "https_enabled", "0")
    assert hostname.get_public_base_url(db) == "http://newscast.local:8080"
    assert hostname.get_share_url(db) == "http://newscast.local:8080"


def test_https_on_omits_default_port(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "https_enabled", "1")
    assert hostname.get_public_base_url(db) == "https://newscast.local"
    assert hostname.get_share_url(db) == "https://newscast.local"
