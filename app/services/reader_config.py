"""Per-account reader device settings (host, folder, push-when-online)."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import User
from app.services import settings, user_settings

NUDGE_KEY = "reader_setup_nudge"

READER_COPY_KEYS = (
    "reader_device",
    "reader_host",
    "reader_upload_path",
    "reader_push_when_online",
    "reader_ssh_port",
    "reader_ssh_user",
    "reader_ssh_password",
)


def username_slug(username: str) -> str:
    raw = (username or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    return cleaned or "user"


def default_upload_folder(device: str) -> str:
    return settings.DEFAULT_KOBO_FOLDER if device == "kobo" else settings.DEFAULT_XTEINK_FOLDER


def normalize_upload_folder(folder: str, device: str) -> str:
    default = default_upload_folder(device)
    raw = (folder or "").strip() or default
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/") or default


def namespaced_upload_path(folder: str, username: str, device: str) -> str:
    """Ensure upload folder ends with /{username}, re-rooting stock/default trees."""
    device = settings.normalize_reader_device(device)
    default = default_upload_folder(device)
    base = normalize_upload_folder(folder, device)
    slug = username_slug(username)
    if base == f"/{slug}" or base.endswith(f"/{slug}"):
        return base
    default_root = default.rstrip("/")
    if base == default_root or base.startswith(default_root + "/"):
        return f"{default_root}/{slug}"
    return f"{base}/{slug}"


def _raw(db: Session, user_id: int, key: str) -> str:
    return user_settings.get_with_fallback(db, int(user_id), key)


def reader_device(db: Session, user_id: int) -> str:
    return settings.normalize_reader_device(_raw(db, user_id, "reader_device"))


def reader_is_kobo(db: Session, user_id: int) -> bool:
    return reader_device(db, user_id) == "kobo"


def reader_host(db: Session, user_id: int) -> str:
    host = (_raw(db, user_id, "reader_host") or "").strip()
    host = host.removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        return host
    if reader_is_kobo(db, user_id):
        return ""
    # Blank personal setting: do not invent a shared mDNS name for every user.
    # Admin still picks up instance fallback via get_with_fallback when present.
    return ""


def reader_upload_dir(db: Session, user_id: int, *, username: str | None = None) -> str:
    device = reader_device(db, user_id)
    stored = (_raw(db, user_id, "reader_upload_path") or "").strip()
    user = db.get(User, int(user_id))
    name = username or (user.username if user else "user")
    return namespaced_upload_path(stored or default_upload_folder(device), name, device)


def reader_push_enabled(db: Session, user_id: int) -> bool:
    return user_settings.flag_enabled(db, int(user_id), "reader_push_when_online")


def reader_ssh_port(db: Session, user_id: int) -> int:
    raw = _raw(db, user_id, "reader_ssh_port").strip()
    if not raw:
        return settings.DEFAULT_KOBO_SSH_PORT
    try:
        value = int(raw)
    except ValueError:
        return settings.DEFAULT_KOBO_SSH_PORT
    return value if 1 <= value <= 65535 else settings.DEFAULT_KOBO_SSH_PORT


def reader_ssh_user(db: Session, user_id: int) -> str:
    return _raw(db, user_id, "reader_ssh_user").strip() or settings.DEFAULT_KOBO_SSH_USER


def reader_ssh_password(db: Session, user_id: int) -> str:
    return user_settings.get_with_fallback(db, int(user_id), "reader_ssh_password")


def needs_setup_nudge(db: Session, user_id: int) -> bool:
    return user_settings.get_value(db, int(user_id), NUDGE_KEY).strip() == "1"


def clear_setup_nudge(db: Session, user_id: int) -> None:
    user_settings.clear_value(db, int(user_id), NUDGE_KEY)


def set_setup_nudge(db: Session, user_id: int) -> None:
    user_settings.set_value(db, int(user_id), NUDGE_KEY, "1")


def _mirror_admin_instance(db: Session, user: User, key: str, value: str) -> None:
    """Keep instance settings aligned with the admin's personal reader for migration fallback."""
    if user.role == "admin":
        settings.set_value(db, key, value)


def _clear_admin_instance(db: Session, user: User, key: str) -> None:
    if user.role == "admin":
        settings.clear_value(db, key)


def save_reader_settings(
    db: Session,
    user: User,
    *,
    reader_device_value: str,
    reader_host_value: str,
    reader_upload_path_value: str,
    reader_push_when_online: bool,
    reader_ssh_port_value: str = "",
    reader_ssh_user_value: str = "",
    reader_ssh_password_value: str = "",
    clear_reader_ssh_password: bool = False,
) -> None:
    uid = int(user.id)
    device = settings.normalize_reader_device(reader_device_value)
    user_settings.set_value(db, uid, "reader_device", device)
    _mirror_admin_instance(db, user, "reader_device", device)

    host = reader_host_value.strip().removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        user_settings.set_value(db, uid, "reader_host", host)
        _mirror_admin_instance(db, user, "reader_host", host)
    elif device == "kobo":
        user_settings.clear_value(db, uid, "reader_host")
        _clear_admin_instance(db, user, "reader_host")
    else:
        host = settings.DEFAULT_XTEINK_HOST
        user_settings.set_value(db, uid, "reader_host", host)
        _mirror_admin_instance(db, user, "reader_host", host)

    folder = namespaced_upload_path(reader_upload_path_value, user.username, device)
    user_settings.set_value(db, uid, "reader_upload_path", folder)
    _mirror_admin_instance(db, user, "reader_upload_path", folder)

    push = "1" if reader_push_when_online else "0"
    user_settings.set_value(db, uid, "reader_push_when_online", push)
    _mirror_admin_instance(db, user, "reader_push_when_online", push)

    if reader_ssh_port_value.strip():
        try:
            port = int(reader_ssh_port_value)
        except ValueError as exc:
            raise ValueError("SSH port must be a number.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535.")
        user_settings.set_value(db, uid, "reader_ssh_port", str(port))
        _mirror_admin_instance(db, user, "reader_ssh_port", str(port))
    ssh_user = reader_ssh_user_value.strip() or settings.DEFAULT_KOBO_SSH_USER
    user_settings.set_value(db, uid, "reader_ssh_user", ssh_user)
    _mirror_admin_instance(db, user, "reader_ssh_user", ssh_user)

    if clear_reader_ssh_password:
        user_settings.clear_value(db, uid, "reader_ssh_password")
        _clear_admin_instance(db, user, "reader_ssh_password")
    elif reader_ssh_password_value.strip():
        secret = reader_ssh_password_value.strip()
        user_settings.set_value(db, uid, "reader_ssh_password", secret)
        _mirror_admin_instance(db, user, "reader_ssh_password", secret)

    clear_setup_nudge(db, uid)


def copy_admin_reader_settings(db: Session, target: User) -> None:
    """Copy admin reader device settings onto target, namespace folder, and set setup nudge."""
    admin = (
        db.query(User)
        .filter(User.role == "admin", User.active.is_(True))
        .order_by(User.id.asc())
        .first()
    )
    if admin is None or admin.id == target.id:
        return
    for key in READER_COPY_KEYS:
        value = user_settings.get_with_fallback(db, admin.id, key)
        if value.strip():
            user_settings.set_value(db, target.id, key, value)
        else:
            user_settings.clear_value(db, target.id, key)

    device = reader_device(db, target.id)
    folder = namespaced_upload_path(
        user_settings.get_value(db, target.id, "reader_upload_path")
        or default_upload_folder(device),
        target.username,
        device,
    )
    user_settings.set_value(db, target.id, "reader_upload_path", folder)
    set_setup_nudge(db, target.id)


def settings_context(db: Session, user: User) -> dict:
    uid = int(user.id)
    device = reader_device(db, uid)
    return {
        "reader_device": device,
        "reader_devices": settings.READER_DEVICES,
        "reader_host": user_settings.get_value(db, uid, "reader_host")
        or ("" if device == "kobo" else _raw(db, uid, "reader_host")),
        "reader_upload_path": reader_upload_dir(db, uid, username=user.username),
        "reader_push_when_online": reader_push_enabled(db, uid),
        "reader_ssh_port": reader_ssh_port(db, uid),
        "reader_ssh_user": reader_ssh_user(db, uid),
        "reader_ssh_password": user_settings.secret_hint(db, uid, "reader_ssh_password"),
        "reader_setup_nudge": needs_setup_nudge(db, uid),
    }
