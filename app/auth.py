import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import DATA_DIR, env
from app.db import get_db
from app.services import settings

COOKIE_NAME = "newscast"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14


def _equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8")) if len(left) == len(right) else False


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


def _encode_session(username: str) -> str:
    body = json.dumps({"u": username, "exp": int(time.time()) + COOKIE_MAX_AGE}, separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{token}.{_sign(body)}"


def user_from_request(request: Request) -> str | None:
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
    return str(user) if user else None


def attach_session(response: Response, username: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _encode_session(username),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
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


def credentials_match(db: Session, username: str, password: str) -> bool:
    expected_user, expected_pass = settings.get_admin_credentials(db)
    return _equal(username, expected_user) and _equal(password, expected_pass)


def is_signed_in(request: Request) -> bool:
    return bool(user_from_request(request))


def require_admin(request: Request, db: Annotated[Session, Depends(get_db)]) -> str:
    user = user_from_request(request)
    if user:
        return user
    if wants_json(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={quote(nxt, safe='/')}"},
    )


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
    if not settings.catalog_login_enabled(db):
        return
    expected_pass = settings.get_value(db, "x3_sync_token")
    if not expected_pass:
        return
    expected_user = settings.catalog_username(db)
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
