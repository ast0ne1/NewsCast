from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import quote, urlparse

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import DATA_DIR, env
from app.db import get_db
from app.services import passwords, settings

COOKIE_NAME = "newscast"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


@dataclass
class SessionUser:
    username: str
    user_id: int | None = None
    role: str = "admin"


_login_failures: dict[str, list[float]] = defaultdict(list)


def _equal(left: str, right: str) -> bool:
    return passwords.secrets_compare(left, right)


def session_secret() -> str:
    if env.session_secret.strip():
        return env.session_secret.strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "session.secret"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    value = secrets.token_hex(32)
    path.write_text(value, encoding="utf-8")
    return value


def _secret_bytes() -> bytes:
    return session_secret().encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(_secret_bytes(), payload, hashlib.sha256).hexdigest()


def _encode_session(*, username: str, user_id: int | None = None, role: str = "admin") -> str:
    body = json.dumps(
        {
            "u": username,
            "uid": user_id,
            "role": role,
            "exp": int(time.time()) + COOKIE_MAX_AGE,
        },
        separators=(",", ":"),
    ).encode()
    token = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{token}.{_sign(body)}"


def session_from_request(request: Request) -> SessionUser | None:
    value = request.cookies.get(COOKIE_NAME)
    if not value or "." not in value:
        return None
    token, signature = value.rsplit(".", 1)
    pad = "=" * (-len(token) % 4)
    try:
        body = base64.urlsafe_b64decode(token + pad)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(body), signature):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(data.get("exp") or 0) < time.time():
        return None
    user = data.get("u")
    if not user:
        return None
    uid = data.get("uid")
    try:
        user_id = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        user_id = None
    role = str(data.get("role") or "admin")
    return SessionUser(username=str(user), user_id=user_id, role=role)


def user_from_request(request: Request) -> str | None:
    session = session_from_request(request)
    return session.username if session else None


def request_is_https(request: Request, db: Session | None = None) -> bool:
    if db is not None and not settings.https_enabled(db):
        return False
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    return proto == "https"


def attach_session(
    response: Response,
    username: str,
    *,
    user_id: int | None = None,
    role: str = "admin",
    secure: bool = False,
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _encode_session(username=username, user_id=user_id, role=role),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def safe_next(value: str | None) -> str:
    if not value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.headers.get("x-requested-with") == "fetch"


def login_rate_limited(request: Request, username: str) -> bool:
    now = time.time()
    keys = [f"ip:{_client_ip(request)}", f"user:{(username or '').strip().casefold()}"]
    for key in keys:
        stamps = [t for t in _login_failures[key] if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = stamps
        if len(stamps) >= LOGIN_MAX_FAILURES:
            return True
    return False


def record_login_failure(request: Request, username: str) -> None:
    now = time.time()
    _login_failures[f"ip:{_client_ip(request)}"].append(now)
    _login_failures[f"user:{(username or '').strip().casefold()}"].append(now)


def clear_login_failures(request: Request, username: str) -> None:
    _login_failures.pop(f"ip:{_client_ip(request)}", None)
    _login_failures.pop(f"user:{(username or '').strip().casefold()}", None)


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def credentials_match(db: Session, username: str, password: str) -> bool:
    """Match against User table when present, else legacy admin settings."""
    from app.models import User

    name = (username or "").strip()
    if not name:
        return False
    row = db.query(User).filter(User.username == name).one_or_none()
    if row is not None:
        if not row.active:
            return False
        if not passwords.verify_password(row.password, password):
            return False
        if passwords.needs_rehash(row.password):
            row.password = passwords.hash_password(password)
            db.commit()
        return True
    expected_user, expected_pass = settings.get_admin_credentials(db)
    if not _equal(name, expected_user):
        return False
    if not passwords.verify_password(expected_pass, password):
        return False
    if passwords.needs_rehash(expected_pass):
        settings.set_value(db, "admin_password", passwords.hash_password(password))
    return True


def resolve_login_identity(db: Session, username: str) -> SessionUser:
    from app.models import User

    name = (username or "").strip()
    row = db.query(User).filter(User.username == name).one_or_none()
    if row is not None:
        return SessionUser(username=row.username, user_id=row.id, role=row.role)
    return SessionUser(username=name, user_id=None, role="admin")


def effective_user_id(session: SessionUser | None, default: int = 1) -> int:
    if session and session.user_id is not None:
        return int(session.user_id)
    return int(default)


def is_signed_in(request: Request) -> bool:
    return session_from_request(request) is not None


def require_login(request: Request, db: Annotated[Session, Depends(get_db)]) -> SessionUser:
    session = session_from_request(request)
    if session:
        return session
    if wants_json(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={quote(nxt, safe='/')}"},
    )


def require_admin(request: Request, db: Annotated[Session, Depends(get_db)]) -> str:
    """Backward-compatible dependency: returns username string."""
    session = require_login(request, db)
    if session.role != "admin":
        # Non-admins may still use most UI; admin-only routes use require_role.
        return session.username
    return session.username


def require_role(*roles: str):
    allowed = set(roles)

    def _dep(request: Request, db: Annotated[Session, Depends(get_db)]) -> SessionUser:
        session = require_login(request, db)
        if session.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return session

    return _dep


def _basic_user_password(encoded: str) -> tuple[str, str]:
    try:
        pad = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + pad).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return "", ""
    username, sep, password = decoded.partition(":")
    if not sep:
        return "", ""
    return username, password


def require_x3_token(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Legacy /opds and /api/x3: authenticate against the admin user's OPDS token."""
    from app.services.users import ensure_admin_user

    admin = ensure_admin_user(db)
    _require_catalog_token_for_user(
        db,
        user_id=admin.id,
        authorization=authorization,
        token=token,
        username_fallback=settings.catalog_username(db),
    )


def require_opds_user_token(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> int:
    """Authenticate /opds/u/{username} and /api/x3/u/{username} against that user's token."""
    from app.models import User

    name = (username or "").strip()
    row = db.query(User).filter(User.username == name).one_or_none()
    if row is None or not row.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid reader credentials",
            headers={"WWW-Authenticate": 'Basic realm="NewsCast"'},
        )
    _require_catalog_token_for_user(
        db,
        user_id=row.id,
        authorization=authorization,
        token=token,
        username_fallback=row.username,
    )
    return row.id


def _require_catalog_token_for_user(
    db: Session,
    *,
    user_id: int,
    authorization: str | None,
    token: str | None,
    username_fallback: str,
) -> None:
    from app.services import user_settings as user_settings_service

    if not settings.catalog_login_enabled(db):
        return
    expected_pass = user_settings_service.get_with_fallback(db, user_id, "x3_sync_token")
    if not expected_pass:
        return
    expected_user = (username_fallback or settings.catalog_username(db)).strip() or "newscast"
    provided = token or ""
    user_ok = True
    if authorization:
        scheme, _, rest = authorization.partition(" ")
        scheme_l = scheme.lower()
        if scheme_l == "bearer":
            provided = rest.strip()
        elif scheme_l == "basic":
            username, provided = _basic_user_password(rest.strip())
            user_ok = _equal(username.casefold(), expected_user.casefold())
    if not user_ok or not _equal(provided, expected_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid reader credentials",
            headers={"WWW-Authenticate": 'Basic realm="NewsCast"'},
        )
