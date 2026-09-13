import pytest

from app.services.library import pretty_size, validate_upload


def test_validate_pdf():
    assert validate_upload("brief.pdf", b"%PDF-1.4 fake") == ".pdf"


def test_validate_epub():
    assert validate_upload("book.epub", b"PK\x03\x04fake") == ".epub"


def test_reject_other_types():
    with pytest.raises(ValueError):
        validate_upload("notes.txt", b"hello")


def test_reject_bad_magic():
    with pytest.raises(ValueError):
        validate_upload("trick.pdf", b"not a pdf")


def test_pretty_size():
    assert pretty_size(512) == "512 B"
    assert pretty_size(2048) == "2.0 KB"
