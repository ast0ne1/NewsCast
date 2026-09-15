from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import UserSetting
from app.services import settings as instance_settings


def get_value(db: Session, user_id: int, key: str, default: str = "") -> str:
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is not None and row.value != "":
        return row.value
    return default


def set_value(db: Session, user_id: int, key: str, value: str) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is None:
        db.add(UserSetting(user_id=user_id, key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    db.commit()


def clear_value(db: Session, user_id: int, key: str) -> None:
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is None:
        return
    db.delete(row)
    db.commit()


def get_with_fallback(
    db: Session,
    user_id: int,
    key: str,
    *,
    instance_fallback_keys: list[str] | None = None,
    default: str = "",
) -> str:
    """Return user setting; if blank, try instance keys in order."""
    value = get_value(db, user_id, key, default="")
    if value.strip():
        return value
    for inst_key in instance_fallback_keys or [key]:
        inst = instance_settings.get_value(db, inst_key)
        if inst.strip():
            return inst
    return default


def secret_hint(db: Session, user_id: int, key: str) -> dict:
    value = get_with_fallback(db, user_id, key)
    source = "user" if get_value(db, user_id, key).strip() else instance_settings.get_source(db, key)
    return {
        "set": bool(value),
        "source": source or "default",
        "hint": instance_settings.mask_secret(value) if value else "",
    }


def flag_enabled(db: Session, user_id: int, key: str) -> bool:
    raw = get_with_fallback(db, user_id, key).strip().lower()
    return raw in {"1", "true", "on", "yes"}