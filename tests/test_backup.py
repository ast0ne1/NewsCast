import sqlite3

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
    monkeypatch.setattr(backup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(backup, "BACKUPS_DIR", data / "backups")

    dest = backup.write_backup()
    assert dest.exists()
    (library / "queued.txt").write_text("changed", encoding="utf-8")
    backup.restore_backup(dest)
    assert (library / "queued.txt").read_text(encoding="utf-8") == "keep-me"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "PORT=8080\n"
    assert (data / "newscast.db").exists()
