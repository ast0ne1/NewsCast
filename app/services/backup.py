from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from sqlalchemy import text

from app import __version__
from app.config import BACKUPS_DIR, BRIEFING_DIR, DATA_DIR, LIBRARY_DIR, ROOT_DIR, TLS_DIR
from app.db import engine

FORMAT = "newscast-backup"
FORMAT_VERSION = 1
CACHE_DIR = DATA_DIR / "cache"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _checkpoint_db() -> Path | None:
    db_path = DATA_DIR / "newscast.db"
    if not db_path.exists():
        return None
    try:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception:
        raw = sqlite3.connect(db_path)
        try:
            raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            raw.close()
    return db_path


def _add_tree(archive: zipfile.ZipFile, root: Path, prefix: str) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file():
            archive.write(path, Path(prefix) / path.relative_to(root))


def _clear_tree(root: Path) -> None:
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _restore_tree(archive: zipfile.ZipFile, names: set[str], prefix: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _clear_tree(dest)
    lead = f"{prefix}/"
    for name in names:
        if not name.startswith(lead) or name.endswith("/"):
            continue
        target = dest / name[len(lead) :]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(name))


def write_backup(dest: Path | None = None) -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    dest = dest or (BACKUPS_DIR / f"newscast-backup-{_timestamp()}.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    db_path = _checkpoint_db()
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
    }
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        if db_path and db_path.exists():
            archive.write(db_path, "newscast.db")
        env_path = ROOT_DIR / ".env"
        if env_path.exists():
            archive.write(env_path, ".env")
        _add_tree(archive, LIBRARY_DIR, "library")
        _add_tree(archive, BRIEFING_DIR, "briefings")
        _add_tree(archive, CACHE_DIR, "cache")
        _add_tree(archive, TLS_DIR, "tls")
    return dest


def backup_bytes() -> bytes:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    temp = BACKUPS_DIR / f".tmp-{_timestamp()}.zip"
    try:
        write_backup(temp)
        return temp.read_bytes()
    finally:
        if temp.exists():
            temp.unlink()


def latest_backup() -> Path | None:
    if not BACKUPS_DIR.exists():
        return None
    zips = sorted(BACKUPS_DIR.glob("newscast-backup-*.zip"), key=lambda path: path.stat().st_mtime)
    return zips[-1] if zips else None


def restore_backup(payload: bytes | Path) -> None:
    raw = payload.read_bytes() if isinstance(payload, Path) else payload
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        names = set(archive.namelist())
        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") and manifest.get("format") != FORMAT:
                raise ValueError("That file is not a NewsCast backup.")
        if "newscast.db" not in names:
            raise ValueError("Backup is missing the database.")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TLS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            engine.dispose()
        except Exception:
            pass
        db_path = DATA_DIR / "newscast.db"
        db_path.write_bytes(archive.read("newscast.db"))
        for stale in (DATA_DIR / "newscast.db-wal", DATA_DIR / "newscast.db-shm"):
            if stale.exists():
                stale.unlink()
        if ".env" in names:
            (ROOT_DIR / ".env").write_bytes(archive.read(".env"))
        _restore_tree(archive, names, "library", LIBRARY_DIR)
        if any(name.startswith("briefings/") for name in names):
            _restore_tree(archive, names, "briefings", BRIEFING_DIR)
        if any(name.startswith("cache/") for name in names):
            _restore_tree(archive, names, "cache", CACHE_DIR)
        if any(name.startswith("tls/") for name in names):
            _restore_tree(archive, names, "tls", TLS_DIR)
