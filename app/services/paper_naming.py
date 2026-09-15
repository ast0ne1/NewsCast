from __future__ import annotations

import re
from datetime import date

from sqlalchemy.orm import Session

from app.services import hostname, settings

DEFAULT_TITLE_PATTERN = "NewsCast - {hostname} {instance} {date}"
DEFAULT_CATEGORY_TITLE_PATTERN = "NewsCast - {hostname} {instance} {category} {date}"
TITLE_TOKENS = (
    ("product", "NewsCast"),
    ("hostname", "Device hostname"),
    ("instance", "Device instance name"),
    ("label", "Paper label"),
    ("date", "Paper date"),
)
CATEGORY_TITLE_TOKENS = (
    ("product", "NewsCast"),
    ("hostname", "Device hostname"),
    ("instance", "Device instance name"),
    ("label", "Paper label"),
    ("category", "Category name"),
    ("date", "Paper date"),
)
DATE_FORMATS = [
    ("iso", "YYYY-MM-DD (2026-09-14)"),
    ("dmy", "DD-MM-YYYY (14-09-2026)"),
    ("friendly", "14 Sep 2026"),
    ("ordinal", "Sept. 14th 2026"),
]
DATE_FORMAT_IDS = {value for value, _label in DATE_FORMATS}
DEFAULT_DATE_FORMAT = "iso"

_TOKEN_RE = re.compile(r"\{(product|hostname|instance|label|category|date)\}", re.IGNORECASE)
_BAD_FILE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE_RE = re.compile(r"\s+")
_DASH_RE = re.compile(r"(?:\s*-\s*){2,}")
_ORDINAL_MONTHS = (
    "Jan.",
    "Feb.",
    "Mar.",
    "Apr.",
    "May",
    "Jun.",
    "Jul.",
    "Aug.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
)


def normalize_date_format(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in DATE_FORMAT_IDS else DEFAULT_DATE_FORMAT


def reader_date_format(db: Session) -> str:
    return normalize_date_format(settings.get_value(db, "reader_date_format"))


def normalize_title_pattern(value: str | None) -> str:
    raw = (value or "").strip()
    return raw[:120] if raw else DEFAULT_TITLE_PATTERN


def normalize_category_title_pattern(value: str | None) -> str:
    raw = (value or "").strip()
    return raw[:120] if raw else DEFAULT_CATEGORY_TITLE_PATTERN


def reader_title_pattern(db: Session) -> str:
    return normalize_title_pattern(settings.get_value(db, "reader_title_pattern"))


def reader_category_title_pattern(db: Session) -> str:
    return normalize_category_title_pattern(settings.get_value(db, "reader_category_title_pattern"))


def normalize_paper_label(value: str | None) -> str:
    return (value or "").strip()[:80]


def reader_paper_label(db: Session) -> str:
    """Custom Reader label for the {label} token (never the Device instance name)."""
    return normalize_paper_label(settings.get_value(db, "reader_paper_label"))


def reader_instance_name(db: Session) -> str:
    """Device instance name for the {instance} token."""
    return normalize_paper_label(settings.get_value(db, "instance_name"))


def _ordinal(day: int) -> str:
    if 10 <= (day % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_paper_date(day: date, style: str | None = None) -> str:
    key = normalize_date_format(style)
    if key == "dmy":
        return day.strftime("%d-%m-%Y")
    if key == "friendly":
        return day.strftime("%d %b %Y")
    if key == "ordinal":
        return f"{_ORDINAL_MONTHS[day.month - 1]} {_ordinal(day.day)} {day.year}"
    return day.isoformat()


def date_format_previews(day: date | None = None) -> dict[str, str]:
    sample = day or date.today()
    return {key: format_paper_date(sample, key) for key, _label in DATE_FORMATS}


def _collapse_name(value: str) -> str:
    text = _SPACE_RE.sub(" ", (value or "").strip())
    text = _DASH_RE.sub(" - ", text)
    text = re.sub(r"^\s*-\s*|\s*-\s*$", "", text)
    return _SPACE_RE.sub(" ", text).strip(" -")


def render_paper_name(
    pattern: str,
    *,
    day: date,
    date_style: str | None = None,
    hostname_value: str = "",
    instance: str = "",
    label: str = "",
    category: str = "",
    product: str = "NewsCast",
) -> str:
    date_text = format_paper_date(day, date_style)
    values = {
        "product": (product or "NewsCast").strip() or "NewsCast",
        "hostname": (hostname_value or "").strip(),
        "instance": (instance or "").strip(),
        "label": (label or "").strip(),
        "category": (category or "").strip(),
        "date": date_text,
    }

    def repl(match: re.Match[str]) -> str:
        return values.get(match.group(1).lower(), "")

    filled = _TOKEN_RE.sub(repl, pattern or DEFAULT_TITLE_PATTERN)
    return _collapse_name(filled) or f"NewsCast {date_text}"


def safe_filename(name: str, *, suffix: str = "epub") -> str:
    stem = _BAD_FILE_RE.sub("-", name or "")
    stem = stem.replace("·", "-")
    stem = _SPACE_RE.sub(" ", stem).strip(" .")
    stem = _collapse_name(stem) or "NewsCast"
    ext = (suffix or "epub").lstrip(".")
    return f"{stem}.{ext}"


def paper_display_title(db: Session, day: date) -> str:
    return render_paper_name(
        reader_title_pattern(db),
        day=day,
        date_style=reader_date_format(db),
        hostname_value=hostname.normalize_hostname(settings.get_value(db, "device_hostname")),
        instance=reader_instance_name(db),
        label=reader_paper_label(db),
    )


def paper_category_display_title(db: Session, day: date, category_label: str) -> str:
    return render_paper_name(
        reader_category_title_pattern(db),
        day=day,
        date_style=reader_date_format(db),
        hostname_value=hostname.normalize_hostname(settings.get_value(db, "device_hostname")),
        instance=reader_instance_name(db),
        label=reader_paper_label(db),
        category=(category_label or "").strip(),
    )


def paper_download_name(db: Session, day: date, suffix: str = "epub") -> str:
    return safe_filename(paper_display_title(db, day), suffix=suffix)


def day_from_briefing_path(path_stem: str) -> date | None:
    raw = (path_stem or "").strip()
    if raw.lower().startswith("news-"):
        raw = raw[5:]
    day_text = raw[:10]
    try:
        return date.fromisoformat(day_text)
    except ValueError:
        return None


def paper_category_download_name(db: Session, day: date, category_label: str, suffix: str = "epub") -> str:
    return safe_filename(paper_category_display_title(db, day, category_label), suffix=suffix)
