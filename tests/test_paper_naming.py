from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services import paper_naming, settings


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_format_paper_date_styles():
    day = date(2026, 9, 14)
    assert paper_naming.format_paper_date(day, "iso") == "2026-09-14"
    assert paper_naming.format_paper_date(day, "dmy") == "14-09-2026"
    assert paper_naming.format_paper_date(day, "friendly") == "14 Sep 2026"
    assert paper_naming.format_paper_date(day, "ordinal") == "Sept. 14th 2026"


def test_render_paper_name_drops_empty_parts():
    day = date(2026, 9, 14)
    assert (
        paper_naming.render_paper_name(
            "NewsCast - {hostname} {instance} {date}",
            day=day,
            date_style="iso",
            hostname_value="",
            instance="Work",
        )
        == "NewsCast - Work 2026-09-14"
    )
    assert (
        paper_naming.render_paper_name(
            "NewsCast - {hostname} {label} {date}",
            day=day,
            date_style="ordinal",
            hostname_value="newscast",
            label="Morning",
        )
        == "NewsCast - newscast Morning Sept. 14th 2026"
    )


def test_safe_filename_and_db_helpers():
    day = date(2026, 9, 14)
    db = _session()
    settings.set_value(db, "instance_name", "Work")
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "reader_date_format", "dmy")
    settings.set_value(db, "reader_title_pattern", "NewsCast - {hostname} {instance} {date}")
    assert paper_naming.paper_display_title(db, day) == "NewsCast - newscast Work 14-09-2026"
    assert paper_naming.paper_download_name(db, day) == "NewsCast - newscast Work 14-09-2026.epub"
    assert paper_naming.safe_filename('NewsCast - Work: "draft"', suffix="epub") == "NewsCast - Work - draft.epub"


def test_instance_and_label_tokens_are_separate():
    day = date(2026, 9, 14)
    db = _session()
    settings.set_value(db, "instance_name", "Work")
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "reader_date_format", "iso")
    settings.set_value(db, "reader_title_pattern", "NewsCast - {instance} {label} {date}")
    assert paper_naming.reader_instance_name(db) == "Work"
    assert paper_naming.reader_paper_label(db) == ""
    assert paper_naming.paper_display_title(db, day) == "NewsCast - Work 2026-09-14"
    settings.set_value(db, "reader_paper_label", "Morning paper")
    assert paper_naming.reader_paper_label(db) == "Morning paper"
    assert paper_naming.paper_display_title(db, day) == "NewsCast - Work Morning paper 2026-09-14"


def test_category_paper_title_uses_separate_pattern():
    day = date(2026, 9, 14)
    db = _session()
    settings.set_value(db, "instance_name", "Work")
    settings.set_value(db, "device_hostname", "newscast")
    settings.set_value(db, "reader_date_format", "iso")
    settings.set_value(db, "reader_title_pattern", "NewsCast - {hostname} {instance} {date}")
    settings.set_value(db, "reader_category_title_pattern", "{product} {category} — {date}")
    assert paper_naming.paper_display_title(db, day) == "NewsCast - newscast Work 2026-09-14"
    assert paper_naming.paper_category_display_title(db, day, "Tech") == "NewsCast Tech — 2026-09-14"
    assert paper_naming.paper_category_download_name(db, day, "Tech") == "NewsCast Tech — 2026-09-14.epub"


def test_render_paper_name_includes_category_token():
    day = date(2026, 9, 14)
    assert (
        paper_naming.render_paper_name(
            "{product} - {category} {date}",
            day=day,
            date_style="iso",
            category="World News",
        )
        == "NewsCast - World News 2026-09-14"
    )
