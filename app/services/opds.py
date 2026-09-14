from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy.orm import Session

from app.models import LibraryFile
from app.services import hostname, settings
from app.services.briefing import available_daily_papers, briefing_title
from app.services.library import media_type_for
from app.services.paper_naming import paper_display_title

ATOM = "http://www.w3.org/2005/Atom"
NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
EPUB_TYPE = "application/epub+zip"


def atom_updated(value: datetime | None = None) -> str:
    when = value or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def briefing_entry_title(db: Session | None = None, when: datetime | date | None = None, instance_name: str = "") -> str:
    if isinstance(when, datetime):
        day = when.astimezone().date() if when.tzinfo else when.date()
    elif isinstance(when, date):
        day = when
    else:
        day = datetime.now().date()
    if db is not None:
        return paper_display_title(db, day)
    date_label = day.strftime("%d %b %Y")
    return f"{briefing_title(instance_name)} — {date_label}"


def _text(parent: Element, tag: str, value: str) -> Element:
    el = SubElement(parent, tag)
    el.text = value
    return el


def _link(parent: Element, *, rel: str, href: str, type_: str | None = None) -> Element:
    el = SubElement(parent, "link")
    el.set("rel", rel)
    el.set("href", href)
    if type_:
        el.set("type", type_)
    return el


def _feed(*, title: str, feed_id: str, updated: str, self_href: str, start_href: str, kind: str) -> Element:
    media = NAV_TYPE if kind == "navigation" else ACQ_TYPE
    feed = Element("feed")
    feed.set("xmlns", ATOM)
    _text(feed, "id", feed_id)
    _text(feed, "title", title)
    _text(feed, "updated", updated)
    _link(feed, rel="self", href=self_href, type_=media)
    _link(feed, rel="start", href=start_href, type_=NAV_TYPE)
    return feed


def _xml(root: Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")


def _base(db: Session) -> str:
    return hostname.get_public_base_url(db).rstrip("/")


def navigation_feed(db: Session) -> str:
    base = _base(db)
    instance = settings.get_value(db, "instance_name")
    updated = atom_updated()
    feed = _feed(
        title=briefing_title(instance),
        feed_id="urn:newscast:opds",
        updated=updated,
        self_href=f"{base}/opds",
        start_href=f"{base}/opds",
        kind="navigation",
    )
    briefing = SubElement(feed, "entry")
    _text(briefing, "id", "urn:newscast:opds:briefing")
    _text(briefing, "title", "Daily Briefings")
    _text(briefing, "updated", updated)
    _link(briefing, rel="subsection", href=f"{base}/opds/briefing", type_=ACQ_TYPE)

    library = SubElement(feed, "entry")
    _text(library, "id", "urn:newscast:opds:library")
    _text(library, "title", "Library")
    _text(library, "updated", updated)
    _link(library, rel="subsection", href=f"{base}/opds/library", type_=ACQ_TYPE)
    return _xml(feed)


def briefing_feed(db: Session) -> str:
    base = _base(db)
    papers = available_daily_papers(days=2)
    latest = datetime.combine(papers[0], datetime.min.time()) if papers else None
    updated = atom_updated(latest)
    feed = _feed(
        title="Daily Briefings",
        feed_id="urn:newscast:opds:briefing",
        updated=updated,
        self_href=f"{base}/opds/briefing",
        start_href=f"{base}/opds",
        kind="acquisition",
    )
    for day in papers:
        title = briefing_entry_title(db, day)
        entry = SubElement(feed, "entry")
        _text(entry, "id", f"urn:newscast:briefing:{day.isoformat()}")
        _text(entry, "title", title)
        _text(entry, "updated", atom_updated(datetime.combine(day, datetime.min.time())))
        _link(
            entry,
            rel=ACQUISITION_REL,
            href=f"{base}/api/x3/news.epub?day={day.isoformat()}",
            type_=EPUB_TYPE,
        )
    return _xml(feed)


def library_feed(db: Session) -> str:
    base = _base(db)
    items = db.query(LibraryFile).order_by(LibraryFile.created_at.desc()).all()
    latest = items[0].created_at if items else None
    updated = atom_updated(latest)
    feed = _feed(
        title="Library",
        feed_id="urn:newscast:opds:library",
        updated=updated,
        self_href=f"{base}/opds/library",
        start_href=f"{base}/opds",
        kind="acquisition",
    )
    for item in items:
        stored = Path(item.stored_name).name
        entry = SubElement(feed, "entry")
        _text(entry, "id", f"urn:newscast:library:{item.id}")
        _text(entry, "title", item.title or item.original_name)
        _text(entry, "updated", atom_updated(item.created_at))
        _link(
            entry,
            rel=ACQUISITION_REL,
            href=f"{base}/api/v1/files/{quote(stored)}",
            type_=media_type_for(Path(stored)),
        )
    return _xml(feed)
