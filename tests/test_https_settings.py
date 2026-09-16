from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services import hostname, settings, tls


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


def test_https_on_includes_app_port(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 8080)
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "https_enabled", "1")
    assert hostname.get_public_base_url(db) == "https://newscast.local:8080"
    assert hostname.get_share_url(db) == "https://newscast.local:8080"


def test_https_on_omits_default_https_port(monkeypatch):
    monkeypatch.setattr(hostname.env, "port", 443)
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "https_enabled", "1")
    assert hostname.get_public_base_url(db) == "https://newscast.local"


def test_ensure_certificate_creates_files_with_sans(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(tls, "CA_CERT", tmp_path / "tls" / "root-ca.pem")
    monkeypatch.setattr(tls, "CA_KEY", tmp_path / "tls" / "root-ca.key")
    monkeypatch.setattr(tls, "SERVER_CERT", tmp_path / "tls" / "server.crt")
    monkeypatch.setattr(tls, "SERVER_KEY", tmp_path / "tls" / "server.key")
    monkeypatch.setattr(tls, "META_PATH", tmp_path / "tls" / "meta.json")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "192.168.1.50")
    db = _session()
    settings.set_value(db, "device_hostname", "newscast")
    paths = tls.ensure_certificate(db)
    assert paths.server_cert.exists()
    assert paths.server_key.exists()
    assert paths.ca_cert.exists()
    status = tls.certificate_status(db)
    assert status.ready
    assert "newscast.local" in status.sans
    assert "localhost" in status.sans
    assert "192.168.1.50" in status.sans
    assert "127.0.0.1" in status.sans
    pem = tls.root_ca_pem_bytes()
    assert b"BEGIN CERTIFICATE" in pem
    assert b"PRIVATE KEY" not in pem


def test_ensure_certificate_is_idempotent_until_hostname_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(tls, "CA_CERT", tmp_path / "tls" / "root-ca.pem")
    monkeypatch.setattr(tls, "CA_KEY", tmp_path / "tls" / "root-ca.key")
    monkeypatch.setattr(tls, "SERVER_CERT", tmp_path / "tls" / "server.crt")
    monkeypatch.setattr(tls, "SERVER_KEY", tmp_path / "tls" / "server.key")
    monkeypatch.setattr(tls, "META_PATH", tmp_path / "tls" / "meta.json")
    monkeypatch.setattr(hostname, "get_lan_ip", lambda: "10.0.0.2")
    db = _session()
    settings.set_value(db, "device_hostname", "pi")
    tls.ensure_certificate(db)
    first = Path(tls.SERVER_CERT).read_bytes()
    tls.ensure_certificate(db)
    assert Path(tls.SERVER_CERT).read_bytes() == first
    settings.set_value(db, "device_hostname", "living-room")
    tls.ensure_certificate(db)
    assert Path(tls.SERVER_CERT).read_bytes() != first
    assert "living-room.local" in tls.certificate_status(db).sans
