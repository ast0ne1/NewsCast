from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

import httpx
from sqlalchemy.orm import Session

from app import __version__
from app.config import ROOT_DIR, UPDATES_DIR, env
from app.services import backup, settings

logger = logging.getLogger("newscast.update")
GITHUB_API = "https://api.github.com"
REQUIRED_FILES = ("app/__init__.py", "app/main.py", "requirements.txt")
CODE_NAMES = ("app", "deploy", "requirements.txt", "README.md", "INSTALL.md", "CHANGELOG.md", ".env.example")
VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")
TAG_RE = re.compile(r"^v?", re.I)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HEADERS = {
    "User-Agent": "NewsCast-updater",
    "Accept": "application/vnd.github+json",
}


def normalize_repo(value: str) -> str:
    raw = (value or "").strip()
    raw = raw.removeprefix("https://github.com/").removeprefix("http://github.com/")
    raw = raw.strip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def parse_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip()
    if text.lower().startswith("v") and text[1:2].isdigit():
        text = text[1:]
    bits = []
    for part in text.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "" and bits:
            break
        bits.append(int(digits or 0))
    return tuple(bits) if bits else (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def versions_match(release_tag: str, packaged: str) -> bool:
    return parse_version(release_tag) == parse_version(packaged)


def extract_packaged_version(text: str) -> str:
    match = VERSION_RE.search(text or "")
    return match.group(1) if match else ""


def repo_from_db(db: Session) -> str:
    return normalize_repo(settings.get_value(db, "github_repo") or env.github_repo)


def last_check(db: Session) -> dict:
    raw = settings.get_value(db, "update_last_check")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_check(db: Session, payload: dict) -> dict:
    payload = {**payload, "checked_at": datetime.now(timezone.utc).isoformat()}
    settings.set_value(db, "update_last_check", json.dumps(payload))
    return payload


def find_release_root(extracted: Path) -> Path | None:
    if (extracted / "app" / "__init__.py").exists() and (extracted / "app" / "main.py").exists():
        return extracted
    for child in extracted.iterdir():
        if child.is_dir() and (child / "app" / "__init__.py").exists() and (child / "app" / "main.py").exists():
            return child
    return None


def validate_zip(zip_path: Path, expected_tag: str = "") -> dict:
    if not zip_path.exists():
        raise ValueError("Update zip is missing.")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        root = ""
        for name in names:
            if name.endswith("app/__init__.py"):
                root = name[: -len("app/__init__.py")]
                break
        if root is None:
            raise ValueError("Zip does not contain app/__init__.py.")
        required = [f"{root}{item}" for item in REQUIRED_FILES]
        missing = [item for item in required if item not in names]
        if missing:
            raise ValueError("Zip is missing " + ", ".join(Path(item).name for item in missing) + ".")
        packaged = extract_packaged_version(archive.read(f"{root}app/__init__.py").decode("utf-8", errors="replace"))
        if not packaged:
            raise ValueError("Could not read the version inside the update.")
        if expected_tag and not versions_match(expected_tag, packaged):
            raise ValueError(f"Release {expected_tag} does not match packaged version {packaged}.")
        if not is_newer(packaged):
            raise ValueError("This release is not newer than the running version.")
    return {"ok": True, "version": packaged, "tag": expected_tag or packaged}


def check_latest(db: Session, download: bool = True) -> dict:
    repo = repo_from_db(db)
    if not repo:
        payload = {
            "ok": False,
            "newer": False,
            "message": "Set the GitHub repository (owner/NewsCast) before checking for updates.",
        }
        return _save_check(db, payload)
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.get(url)
            if response.status_code == 404:
                raise ValueError("No GitHub release found for that repository.")
            response.raise_for_status()
            body = response.json()
    except ValueError as exc:
        return _save_check(db, {"ok": False, "newer": False, "repo": repo, "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.info("github check failed: %s", exc)
        return _save_check(db, {"ok": False, "newer": False, "repo": repo, "message": "Could not reach GitHub."})

    tag = str(body.get("tag_name") or "")
    notes = (body.get("body") or "").strip()
    zip_url = body.get("zipball_url") or ""
    payload = {
        "ok": True,
        "repo": repo,
        "tag": tag,
        "name": body.get("name") or tag,
        "notes": notes,
        "html_url": body.get("html_url") or "",
        "zip_url": zip_url,
        "newer": is_newer(tag) if tag else False,
        "current": __version__,
        "message": "",
    }
    if not tag:
        payload.update({"ok": False, "message": "The latest GitHub release has no version tag."})
        return _save_check(db, payload)
    if not payload["newer"]:
        payload["message"] = f"NewsCast is up to date ({__version__})."
        return _save_check(db, payload)
    if download and zip_url:
        try:
            zip_path = _download_zip(zip_url, tag)
            validate_zip(zip_path, tag)
            payload["zip_path"] = str(zip_path)
            payload["valid"] = True
            payload["message"] = f"Version {tag} is ready to install."
        except Exception as exc:  # noqa: BLE001
            payload.update({"ok": False, "valid": False, "message": str(exc)})
    else:
        payload["message"] = f"Version {tag} is available."
    return _save_check(db, payload)


def _download_zip(zip_url: str, tag: str) -> Path:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    staging = UPDATES_DIR / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    dest = staging / f"newscast-{tag}.zip"
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True, headers=HEADERS) as client:
        response = client.get(zip_url)
        response.raise_for_status()
        dest.write_bytes(response.content)
    return dest


def snapshot_current_code() -> Path:
    previous = UPDATES_DIR / "previous"
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)
    for name in CODE_NAMES:
        source = ROOT_DIR / name
        if not source.exists():
            continue
        target = previous / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
    return previous


def _copy_code_tree(source_root: Path, dest_root: Path) -> None:
    for name in CODE_NAMES:
        source = source_root / name
        if not source.exists():
            continue
        target = dest_root / name
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)


def apply_zip(zip_path: Path, expected_tag: str = "") -> dict:
    info = validate_zip(zip_path, expected_tag)
    extract_to = UPDATES_DIR / "extracted"
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_to.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_to)
    root = find_release_root(extract_to)
    if root is None:
        raise ValueError("Could not find the NewsCast app folder in the zip.")
    snapshot_current_code()
    backup.write_backup()
    _copy_code_tree(root, ROOT_DIR)
    _install_requirements()
    return {"ok": True, "version": info["version"], "message": f"Installed {info['version']}. Restarting…"}


def rollback_code() -> None:
    previous = UPDATES_DIR / "previous"
    if not previous.exists() or not (previous / "app" / "main.py").exists():
        raise ValueError("No previous app snapshot to roll back to.")
    _copy_code_tree(previous, ROOT_DIR)
    _install_requirements()


def _install_requirements() -> None:
    python = Path(sys.executable)
    requirements = ROOT_DIR / "requirements.txt"
    if not requirements.exists():
        return
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        check=False,
        cwd=str(ROOT_DIR),
    )


def install_latest(db: Session) -> dict:
    check = last_check(db)
    zip_path = Path(check.get("zip_path") or "")
    tag = str(check.get("tag") or "")
    if not zip_path.exists():
        check = check_latest(db, download=True)
        zip_path = Path(check.get("zip_path") or "")
        tag = str(check.get("tag") or "")
    if not check.get("newer"):
        raise ValueError(check.get("message") or "No newer release to install.")
    if not zip_path.exists():
        raise ValueError(check.get("message") or "Download the update before installing.")
    return apply_zip(zip_path, tag)


def schedule_restart() -> None:
    Thread(target=_restart_soon, daemon=True, name="newscast-restart").start()


def _restart_soon() -> None:
    import time

    time.sleep(1.2)
    service = Path("/etc/systemd/system/newscast.service")
    if service.exists():
        subprocess.Popen(["sudo", "systemctl", "restart", "newscast"], close_fds=True)
        return
    bat = ROOT_DIR / "run-local.bat"
    if os.name == "nt" and bat.exists():
        subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(ROOT_DIR), close_fds=True)
        return
    os._exit(0)
