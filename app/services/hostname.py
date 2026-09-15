import re
import shutil
import socket
import subprocess
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import ROOT_DIR, env
from app.services import settings

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SET_HOSTNAME = ROOT_DIR / "deploy" / "set-hostname.sh"


def normalize_hostname(raw: str) -> str:
    value = raw.strip().lower().removesuffix(".local").rstrip(".")
    return value


def valid_hostname(name: str) -> bool:
    return bool(name) and HOSTNAME_RE.fullmatch(name) is not None


def _scheme(db: Session) -> str:
    return "https" if settings.https_enabled(db) else "http"


def _host_url(db: Session, host: str) -> str:
    scheme = _scheme(db)
    if scheme == "https":
        return f"{scheme}://{host}.local"
    return f"{scheme}://{host}.local:{env.port}"


def get_public_base_url(db: Session) -> str:
    host = normalize_hostname(settings.get_value(db, "device_hostname"))
    if host:
        return _host_url(db, host)
    public = env.public_base_url.rstrip("/")
    if public and not _is_loopback(public):
        if settings.https_enabled(db) and public.startswith("http://"):
            return "https://" + public[len("http://") :]
        return public
    return get_lan_url(db)


def get_lan_ip() -> str:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        if sock is not None:
            sock.close()
    if not ip or ip.startswith("127."):
        return ""
    return ip


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def get_lan_url(db: Session | None = None) -> str:
    scheme = _scheme(db) if db is not None else "http"
    ip = get_lan_ip()
    if ip:
        if scheme == "https":
            return f"{scheme}://{ip}"
        return f"{scheme}://{ip}:{env.port}"
    public = env.public_base_url.rstrip("/")
    if public and not _is_loopback(public):
        return public
    if scheme == "https":
        return "https://127.0.0.1"
    return f"http://127.0.0.1:{env.port}"


def get_share_url(db: Session) -> str:
    host = normalize_hostname(settings.get_value(db, "device_hostname"))
    if host:
        return _host_url(db, host)
    return get_lan_url(db)


def homescreen_name(db: Session) -> str:
    instance = settings.get_value(db, "instance_name").strip()
    if instance:
        return f"NewsCast {instance}"
    host = normalize_hostname(settings.get_value(db, "device_hostname"))
    if host:
        return f"NewsCast {host.replace('-', ' ').title()}"
    return "NewsCast"


def apply_os_hostname(name: str) -> bool:
    if not SET_HOSTNAME.is_file() or shutil.which("hostnamectl") is None:
        return False
    command = [str(SET_HOSTNAME), name]
    sudo = shutil.which("sudo")
    if sudo:
        command = [sudo, "-n", *command]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True
