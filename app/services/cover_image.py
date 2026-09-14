from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COVER_WIDTH = 1200
COVER_HEIGHT = 1600
PAPER = (248, 244, 235)
INK = (18, 18, 18)
RULE = (40, 40, 40)
MUTED = (70, 70, 70)

_FONT_CANDIDATES = {
    "serif": [
        Path(r"C:\Windows\Fonts\georgia.ttf"),
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf"),
    ],
    "serif_bold": [
        Path(r"C:\Windows\Fonts\georgiab.ttf"),
        Path(r"C:\Windows\Fonts\timesbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
    ],
    "sans": [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ],
}


def _load_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES.get(kind, []):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    return textwrap.wrap(cleaned, width=width, break_long_words=True, break_on_hyphens=True)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    gap: int,
    max_width: int | None = None,
) -> int:
    cursor = y
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=fill)
        box = draw.textbbox((0, 0), line, font=font)
        cursor += (box[3] - box[1]) + gap
        if max_width is not None and cursor - y > max_width:
            break
    return cursor


def _plain(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(cleaned.split())


def _story_title(story: dict) -> str:
    return _plain(story.get("title") or "Untitled") or "Untitled"


def _story_deck(story: dict, limit: int = 160) -> str:
    from app.services.summarize import clean_summary

    raw = clean_summary(_plain(story.get("summary") or ""))
    if not raw:
        return ""
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rsplit(" ", 1)[0] + "…"


def _pick_stories(stories: list[dict]) -> tuple[dict | None, list[dict]]:
    items = [story for story in stories if _story_title(story)]
    if not items:
        return None, []
    return items[0], items[1:5]


def render_newspaper_cover(
    *,
    heading: str,
    date_label: str,
    stories: list[dict],
) -> bytes:
    image = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)

    masthead = _load_font("serif_bold", 78)
    date_font = _load_font("sans", 28)
    kicker = _load_font("sans", 22)
    main_font = _load_font("serif_bold", 54)
    deck_font = _load_font("serif", 28)
    side_font = _load_font("serif_bold", 30)
    side_deck = _load_font("serif", 22)
    footer_font = _load_font("sans", 20)

    margin = 64
    content_w = COVER_WIDTH - (margin * 2)
    y = 56

    brand = " ".join((heading or "NewsCast").split()) or "NewsCast"
    brand_lines = _wrap(brand.upper(), 22)
    for line in brand_lines:
        box = draw.textbbox((0, 0), line, font=masthead)
        draw.text(((COVER_WIDTH - (box[2] - box[0])) // 2, y), line, font=masthead, fill=INK)
        y += (box[3] - box[1]) + 4

    y += 8
    date_text = (date_label or "").strip()
    if date_text:
        box = draw.textbbox((0, 0), date_text, font=date_font)
        draw.text(((COVER_WIDTH - (box[2] - box[0])) // 2, y), date_text, font=date_font, fill=MUTED)
        y += (box[3] - box[1]) + 18

    draw.line((margin, y, COVER_WIDTH - margin, y), fill=RULE, width=4)
    y += 10
    draw.line((margin, y, COVER_WIDTH - margin, y), fill=RULE, width=1)
    y += 28

    main, flanks = _pick_stories(stories)
    if main is None:
        empty = _wrap("No stories in today's paper yet.", 34)
        _draw_lines(draw, empty, x=margin, y=y, font=main_font, fill=INK, gap=10)
    else:
        source = (main.get("source") or "").strip()
        if source:
            draw.text((margin, y), source.upper(), font=kicker, fill=MUTED)
            y += 34

        main_lines = _wrap(_story_title(main), 28)
        y = _draw_lines(draw, main_lines[:5], x=margin, y=y, font=main_font, fill=INK, gap=8)
        y += 16

        deck_lines = _wrap(_story_deck(main, 220), 52)
        y = _draw_lines(draw, deck_lines[:4], x=margin, y=y, font=deck_font, fill=MUTED, gap=6)
        y += 24

        draw.line((margin, y, COVER_WIDTH - margin, y), fill=RULE, width=2)
        y += 24

        if flanks:
            col_gap = 28
            col_w = (content_w - col_gap) // 2
            left = flanks[0:2]
            right = flanks[2:4]
            if not right and len(flanks) > 1:
                left = flanks[0:1]
                right = flanks[1:2]

            left_y = y
            right_y = y
            for story in left:
                left_y = _draw_flank(
                    draw,
                    story,
                    x=margin,
                    y=left_y,
                    width=col_w,
                    title_font=side_font,
                    deck_font=side_deck,
                    kicker_font=kicker,
                )
                left_y += 22
            for story in right:
                right_y = _draw_flank(
                    draw,
                    story,
                    x=margin + col_w + col_gap,
                    y=right_y,
                    width=col_w,
                    title_font=side_font,
                    deck_font=side_deck,
                    kicker_font=kicker,
                )
                right_y += 22
            y = max(left_y, right_y)

    footer_y = COVER_HEIGHT - 70
    draw.line((margin, footer_y, COVER_WIDTH - margin, footer_y), fill=RULE, width=1)
    mark = "NewsCast daily briefing"
    box = draw.textbbox((0, 0), mark, font=footer_font)
    draw.text(((COVER_WIDTH - (box[2] - box[0])) // 2, footer_y + 18), mark, font=footer_font, fill=MUTED)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def _draw_flank(
    draw: ImageDraw.ImageDraw,
    story: dict,
    *,
    x: int,
    y: int,
    width: int,
    title_font: ImageFont.ImageFont,
    deck_font: ImageFont.ImageFont,
    kicker_font: ImageFont.ImageFont,
) -> int:
    char_w = max(18, width // 14)
    source = (story.get("source") or "").strip()
    cursor = y
    if source:
        draw.text((x, cursor), source.upper(), font=kicker_font, fill=MUTED)
        cursor += 28
    title_lines = _wrap(_story_title(story), char_w)
    cursor = _draw_lines(draw, title_lines[:4], x=x, y=cursor, font=title_font, fill=INK, gap=4)
    cursor += 8
    deck_lines = _wrap(_story_deck(story, 120), char_w + 6)
    cursor = _draw_lines(draw, deck_lines[:3], x=x, y=cursor, font=deck_font, fill=MUTED, gap=4)
    return cursor
