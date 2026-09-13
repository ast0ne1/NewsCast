import zipfile
from pathlib import Path

from app.services.update import (
    extract_packaged_version,
    is_newer,
    normalize_repo,
    parse_version,
    refresh_check,
    validate_zip,
    versions_match,
)


def test_normalize_repo_accepts_url_or_slug():
    assert normalize_repo("owner/NewsCast") == "owner/NewsCast"
    assert normalize_repo("https://github.com/owner/NewsCast.git") == "owner/NewsCast"
    assert normalize_repo("not-a-repo") == ""


def test_parse_and_compare_versions():
    assert parse_version("v0.0.0.2") == (0, 0, 0, 2)
    assert is_newer("0.0.0.2", "0.0.0.1")
    assert not is_newer("0.0.0.1", "0.0.0.1")
    assert versions_match("v1.2.0", "1.2.0")


def test_refresh_check_clears_stale_same_version():
    stale = {
        "ok": True,
        "tag": "0.0.0.2",
        "newer": True,
        "valid": True,
        "notes": "Release notes from before the install.",
        "message": "Version 0.0.0.2 is ready to install.",
    }
    fresh = refresh_check(stale, "0.0.0.2")
    assert fresh["newer"] is False
    assert fresh["valid"] is False
    assert "latest" in fresh["message"]
    still_new = refresh_check(stale, "0.0.0.1")
    assert still_new["newer"] is True
    assert still_new["valid"] is True


def test_extract_packaged_version():
    assert extract_packaged_version('__version__ = "1.2.3"\n') == "1.2.3"


def _write_release_zip(path: Path, version: str, nested: bool = True) -> Path:
    root = f"owner-NewsCast-abc123/" if nested else ""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{root}app/__init__.py", f'__version__ = "{version}"\n__author__ = "Adam Stone"\n')
        archive.writestr(f"{root}app/main.py", "app = None\n")
        archive.writestr(f"{root}requirements.txt", "fastapi\n")
    return path


def test_validate_zip_accepts_newer_nested_release(tmp_path):
    zip_path = _write_release_zip(tmp_path / "rel.zip", "9.9.9.9")
    info = validate_zip(zip_path, "9.9.9.9")
    assert info["ok"] is True
    assert info["version"] == "9.9.9.9"


def test_validate_zip_rejects_same_or_older(tmp_path):
    zip_path = _write_release_zip(tmp_path / "old.zip", "0.0.0.1", nested=False)
    try:
        validate_zip(zip_path, "0.0.0.1")
        raise AssertionError("expected older zip to fail")
    except ValueError as exc:
        assert "not newer" in str(exc)


def test_validate_zip_rejects_version_mismatch(tmp_path):
    zip_path = _write_release_zip(tmp_path / "mix.zip", "2.0.0")
    try:
        validate_zip(zip_path, "3.0.0")
        raise AssertionError("expected mismatch to fail")
    except ValueError as exc:
        assert "does not match" in str(exc)


def test_apply_zip_overlays_without_deleting_running_tree(tmp_path, monkeypatch):
    from app.services import update as update_mod

    root = tmp_path / "install"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text('__version__ = "0.0.0.1"\n', encoding="utf-8")
    (app_dir / "main.py").write_text("old = True\n", encoding="utf-8")
    (app_dir / "stale.py").write_text("gone\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    pycache = app_dir / "__pycache__"
    pycache.mkdir()
    locked = pycache / "main.cpython-312.pyc"
    locked.write_bytes(b"locked")

    zip_path = _write_release_zip(tmp_path / "rel.zip", "9.9.9.9")
    monkeypatch.setattr(update_mod, "ROOT_DIR", root)
    monkeypatch.setattr(update_mod, "UPDATES_DIR", tmp_path / "updates")
    monkeypatch.setattr(update_mod.backup, "write_backup", lambda: tmp_path / "backup.zip")
    monkeypatch.setattr(update_mod, "_install_requirements", lambda: None)

    info = update_mod.apply_zip(zip_path, "9.9.9.9")
    assert info["ok"] is True
    assert (app_dir / "__init__.py").read_text(encoding="utf-8").find("9.9.9.9") >= 0
    assert (app_dir / "main.py").read_text(encoding="utf-8") == "app = None\n"
    assert not (app_dir / "stale.py").exists()
    assert locked.exists()
