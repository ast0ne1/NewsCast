from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services import hostname, settings


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_normalize_strips_local_suffix():
    assert hostname.normalize_hostname("NewsCast.local") == "newscast"
    assert hostname.normalize_hostname(" living-room ") == "living-room"


def test_valid_hostname():
    assert hostname.valid_hostname("newscast")
    assert hostname.valid_hostname("pi")
    assert not hostname.valid_hostname("")
    assert not hostname.valid_hostname("-bad")
    assert not hostname.valid_hostname("has space")


def test_public_base_url_uses_hostname(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname.env, "public_base_url", "http://127.0.0.1:8080")
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    assert hostname.get_public_base_url(db) == "http://newscast.local:8080"


def test_public_base_url_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname.env, "public_base_url", "http://192.168.1.10:8080")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "192.168.1.20")
    db = _session()
    assert hostname.get_public_base_url(db) == "http://192.168.1.10:8080"


def test_public_base_url_uses_lan_when_env_is_loopback(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname.env, "public_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "192.168.0.223")
    db = _session()
    assert hostname.get_public_base_url(db) == "http://192.168.0.223:8080"


def test_share_url_uses_hostname(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "192.168.1.20")
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    assert hostname.get_share_url(db) == "http://newscast.local:8080"
    assert hostname.get_lan_url() == "http://192.168.1.20:8080"


def test_homescreen_name_uses_instance():
    db = _session()
    settings.set_value(db, "instance_name", "Home")
    assert hostname.homescreen_name(db) == "NewsCast Home"


def test_homescreen_name_falls_back_to_hostname():
    db = _session()
    settings.set_value(db, "device_hostname", "living-room")
    assert hostname.homescreen_name(db) == "NewsCast Living Room"


def test_share_url_uses_lan_ip_when_public_is_loopback(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    monkeypatch.setattr(hostname.env, "public_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "192.168.1.20")
    db = _session()
    assert hostname.get_share_url(db) == "http://192.168.1.20:8080"
