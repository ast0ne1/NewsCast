def test_serve_passes_ssl_when_https_enabled(tmp_path, monkeypatch):
    from app import serve
    from app.services import tls

    monkeypatch.setattr(tls, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(tls, "CA_CERT", tmp_path / "tls" / "root-ca.pem")
    monkeypatch.setattr(tls, "CA_KEY", tmp_path / "tls" / "root-ca.key")
    monkeypatch.setattr(tls, "SERVER_CERT", tmp_path / "tls" / "server.crt")
    monkeypatch.setattr(tls, "SERVER_KEY", tmp_path / "tls" / "server.key")
    monkeypatch.setattr(tls, "META_PATH", tmp_path / "tls" / "meta.json")

    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(serve, "init_db", lambda: None)
    monkeypatch.setattr(serve, "SessionLocal", FakeSession)
    monkeypatch.setattr(serve.settings, "https_enabled", lambda _db: True)

    def fake_ensure(_db):
        (tmp_path / "tls").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tls" / "server.crt").write_text("cert", encoding="utf-8")
        (tmp_path / "tls" / "server.key").write_text("key", encoding="utf-8")
        return tls.paths()

    monkeypatch.setattr(tls, "ensure_certificate", fake_ensure)

    captured: dict = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    serve.main()
    assert captured.get("ssl_certfile") == str(tmp_path / "tls" / "server.crt")
    assert captured.get("ssl_keyfile") == str(tmp_path / "tls" / "server.key")


def test_serve_http_when_https_disabled(monkeypatch):
    from app import serve

    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(serve, "init_db", lambda: None)
    monkeypatch.setattr(serve, "SessionLocal", FakeSession)
    monkeypatch.setattr(serve.settings, "https_enabled", lambda _db: False)

    captured: dict = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    serve.main()
    assert captured.get("ssl_certfile") is None
    assert captured.get("ssl_keyfile") is None
