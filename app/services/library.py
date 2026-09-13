from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import LIBRARY_DIR
from app.models import LibraryFile, utcnow
from app.services.briefing import enqueue_sync_file

LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_TYPES = {
    ".pdf": (b"%PDF", "application/pdf"),
    ".epub": (b"PK", "application/epub+zip"),
}


def pretty_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in ALLOWED_TYPES:
        return ALLOWED_TYPES[suffix][1]
    if suffix == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return (cleaned or "document")[:60]


def validate_upload(filename: str, data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_TYPES:
        raise ValueError("Choose an EPUB or PDF file.")
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File is larger than 50 MB.")
    magic, _media = ALLOWED_TYPES[suffix]
    if not data.startswith(magic):
        raise ValueError("That file does not look like a valid EPUB or PDF.")
    return suffix


def add_library_file(db: Session, filename: str, data: bytes, title: str = "") -> LibraryFile:
    suffix = validate_upload(filename, data)
    stored = f"{utcnow().strftime('%Y%m%d-%H%M%S')}-{_safe_stem(filename)}{suffix}"
    path = LIBRARY_DIR / stored
    path.write_bytes(data)
    display = (title or "").strip() or Path(filename).stem
    item = LibraryFile(
        title=display[:200],
        original_name=Path(filename).name[:260],
        stored_name=stored,
        size=len(data),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    enqueue_sync_file(db, path, item.original_name)
    return item


def enqueue_library_file(db: Session, item: LibraryFile):
    path = LIBRARY_DIR / item.stored_name
    if not path.exists():
        raise FileNotFoundError("File is missing from the library folder.")
    return enqueue_sync_file(db, path, item.original_name)


def delete_library_file(db: Session, item: LibraryFile) -> None:
    path = LIBRARY_DIR / item.stored_name
    if path.exists():
        path.unlink()
    db.delete(item)
    db.commit()
