import sqlite3
import tempfile
import zipfile

from app.services import backup


def test_backup_round_trip(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    library = data / "library"
    library.mkdir()
    (library / "queued.txt").write_text("keep-me", encoding="utf-8")
    sqlite3.connect(data / "newscast.db").close()
    (tmp_path / ".env").write_text("PORT=8080\n", encoding="utf-8")
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "LIBRARY_DIR", library)
    monkeypatch.setattr(backup, "BRIEFING_DIR", data / "briefings")
    monkeypatch.setattr(backup, "CACHE_DIR", data / "cache")
    monkeypatch.setattr(backup, "TLS_DIR", data / "tls")
    monkeypatch.setattr(backup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(backup, "BACKUPS_DIR", data / "backups")

    dest = backup.write_backup()
    assert dest.exists()
    (library / "queued.txt").write_text("changed", encoding="utf-8")
    backup.restore_backup(dest)
    assert (library / "queued.txt").read_text(encoding="utf-8") == "keep-me"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "PORT=8080\n"
    assert (data / "newscast.db").exists()


def test_backup_nested_library_and_briefings(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    library = data / "library"
    briefings = data / "briefings"
    cache = data / "cache" / "feeds"
    tls = data / "tls"
    (library / "1").mkdir(parents=True)
    (library / "3").mkdir(parents=True)
    (briefings / "2").mkdir(parents=True)
    (briefings / "3").mkdir(parents=True)
    cache.mkdir(parents=True)
    tls.mkdir(parents=True)
    (library / "1" / "admin.epub").write_bytes(b"PK\x03\x04admin")
    (library / "3" / "doc.epub").write_bytes(b"PK\x03\x04")
    (briefings / "2" / "news-2026-09-14.epub").write_bytes(b"paper-2")
    (briefings / "3" / "news-2026-09-14.epub").write_bytes(b"paper")
    (cache / "abc.txt").write_text("feed-body", encoding="utf-8")
    (tls / "root-ca.pem").write_text("CA-CERT", encoding="utf-8")
    (tls / "server.crt").write_text("SERVER-CERT", encoding="utf-8")
    # Minimal sqlite so the zip includes a DB blob (users table appears after real migrations).
    conn = sqlite3.connect(data / "newscast.db")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'admin')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "LIBRARY_DIR", library)
    monkeypatch.setattr(backup, "BRIEFING_DIR", briefings)
    monkeypatch.setattr(backup, "CACHE_DIR", data / "cache")
    monkeypatch.setattr(backup, "TLS_DIR", tls)
    monkeypatch.setattr(backup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(backup, "BACKUPS_DIR", data / "backups")

    dest = backup.write_backup()
    with zipfile.ZipFile(dest) as archive:
        names = set(archive.namelist())
        assert "library/1/admin.epub" in names
        assert "library/3/doc.epub" in names
        assert "briefings/2/news-2026-09-14.epub" in names
        assert "briefings/3/news-2026-09-14.epub" in names
        assert "cache/feeds/abc.txt" in names
        assert "tls/root-ca.pem" in names
        assert "tls/server.crt" in names
        assert "newscast.db" in names
        db_bytes = archive.read("newscast.db")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(db_bytes)
        tmp_path_db = tmp.name
    check = sqlite3.connect(tmp_path_db)
    try:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "users" in tables
    finally:
        check.close()

    (library / "3" / "doc.epub").write_bytes(b"changed")
    (briefings / "3" / "news-2026-09-14.epub").write_bytes(b"x")
    (cache / "abc.txt").write_text("gone", encoding="utf-8")
    (tls / "root-ca.pem").write_text("changed", encoding="utf-8")
    backup.restore_backup(dest)
    assert (library / "3" / "doc.epub").read_bytes().startswith(b"PK")
    assert (briefings / "2" / "news-2026-09-14.epub").read_bytes() == b"paper-2"
    assert (briefings / "3" / "news-2026-09-14.epub").read_bytes() == b"paper"
    assert (data / "cache" / "feeds" / "abc.txt").read_text(encoding="utf-8") == "feed-body"
    assert (tls / "root-ca.pem").read_text(encoding="utf-8") == "CA-CERT"
