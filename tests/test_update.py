import zipfile
from pathlib import Path

from app.services.update import (
    extract_packaged_version,
    is_newer,
    normalize_repo,
    parse_version,
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
