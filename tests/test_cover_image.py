from pathlib import Path
from zipfile import ZipFile

from app.services.briefing import write_epub
from app.services.cover_image import render_newspaper_cover


def test_render_newspaper_cover_is_jpeg():
    data = render_newspaper_cover(
        heading="NewsCast · Work",
        date_label="14 Sep 2026",
        stories=[
            {
                "title": "Main headline about markets and rates",
                "summary": "Markets rose after the bank held rates steady.",
                "source": "BBC World",
            },
            {
                "title": "Side story one",
                "summary": "A short deck for the left column.",
                "source": "Reuters",
            },
            {
                "title": "Side story two",
                "summary": "A short deck for the right column.",
                "source": "AP",
            },
        ],
    )
    assert data[:3] == b"\xff\xd8\xff"
    assert len(data) > 5000


def test_write_epub_includes_cover_image(tmp_path: Path):
    dest = tmp_path / "news.epub"
    write_epub(
        {
            "title": "NewsCast · Work",
            "generated_at": "2026-09-14T06:30:00Z",
            "paper_date": "2026-09-14",
            "stories": [
                {
                    "id": "1",
                    "title": "Lead story",
                    "summary": "Lead deck for the front page.",
                    "source": "BBC",
                    "url": "https://example.com/a",
                    "published_label": "14 Sep 2026",
                    "category": "news",
                    "category_label": "World News",
                    "saved": False,
                },
                {
                    "id": "2",
                    "title": "Flank story",
                    "summary": "Secondary deck.",
                    "source": "Reuters",
                    "url": "https://example.com/b",
                    "published_label": "14 Sep 2026",
                    "category": "news",
                    "category_label": "World News",
                    "saved": False,
                },
            ],
        },
        dest,
    )
    with ZipFile(dest) as archive:
        names = archive.namelist()
        assert any(name.endswith("cover.jpg") for name in names)
        cover_img = next(name for name in names if name.endswith("cover.jpg"))
        assert archive.read(cover_img)[:3] == b"\xff\xd8\xff"
        opf = archive.read("EPUB/content.opf").decode("utf-8", errors="ignore")
        assert 'name="cover"' in opf
        cover_html = archive.read("EPUB/cover.xhtml").decode("utf-8", errors="ignore")
        assert "cover.jpg" in cover_html
