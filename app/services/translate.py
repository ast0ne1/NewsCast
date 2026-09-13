from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger("newscast.translate")
GTX_URL = "https://translate.googleapis.com/translate_a/single"
CHROME_URL = "https://clients5.google.com/translate_a/t"
CHUNK_CHARS = 4200
TIMEOUT = httpx.Timeout(15.0, connect=6.0)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://translate.google.com/",
}
TITLE_MARK = "[[T]]"
BODY_MARK = "[[B]]"
NORDIC_LETTERS = re.compile(r"[æøåÆØÅäöÄÖ]")
NORDIC_WORDS = re.compile(
    r"\b(og|ikke|efter|blev|denne|dette|lukkede|mand|døden|på|af|til|fra|har|er)\b",
    re.I,
)
ENGLISH_HINT = re.compile(
    r"\b(the|and|will|with|from|that|this|was|were|been|into|their|have|has|closed|wins|died|briefly|suspected)\b",
    re.I,
)
LANG_CODE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")


def looks_untranslated(text: str) -> bool:
    if not text:
        return False
    if NORDIC_WORDS.search(text):
        return True
    # Nordic letters in names (Bælt, Søe) are fine once the sentence is English.
    return bool(NORDIC_LETTERS.search(text) and not ENGLISH_HINT.search(text))


def translate_to_english(text: str) -> str:
    source = (text or "").strip()
    if not source:
        return ""
    parts = [_translate_chunk(chunk) for chunk in _chunks(source)]
    if any(part is None for part in parts):
        return source
    return " ".join(part.strip() for part in parts if part).strip() or source


def translate_story(title: str, excerpt: str) -> tuple[str, str]:
    title = (title or "").strip()
    excerpt = (excerpt or "").strip()
    if excerpt:
        packed = f"{TITLE_MARK}\n{title}\n{BODY_MARK}\n{excerpt}"
        english = translate_to_english(packed)
        parsed = _unpack_story(english, title, excerpt)
        if parsed:
            return parsed
    english_title = translate_to_english(title) or title
    english_excerpt = translate_to_english(excerpt) if excerpt else ""
    return english_title.strip() or title, english_excerpt


def _chunks(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    pieces: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= CHUNK_CHARS:
            pieces.append(remaining)
            break
        cut = remaining.rfind(" ", 0, CHUNK_CHARS)
        if cut < CHUNK_CHARS // 2:
            cut = CHUNK_CHARS
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [piece for piece in pieces if piece]


def _translate_chunk(text: str) -> str | None:
    for attempt, requester in enumerate((_post_gtx, _get_chrome)):
        result = requester(text)
        if result:
            return result
        time.sleep(0.6 * (attempt + 1))
    logger.warning("translate failed after Google fallbacks")
    return None


def _post_gtx(text: str) -> str | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.post(
                GTX_URL,
                params={"client": "gtx", "sl": "auto", "tl": "en", "dt": "t"},
                data={"q": text},
            )
            if response.status_code == 429:
                logger.info("gtx translate rate-limited")
                return None
            response.raise_for_status()
            return _parse_payload(response.json())
    except Exception as exc:  # noqa: BLE001
        logger.info("gtx translate failed: %s", exc)
        return None


def _get_chrome(text: str) -> str | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.get(
                CHROME_URL,
                params={"client": "dict-chrome-ex", "sl": "auto", "tl": "en", "q": text},
            )
            if response.status_code == 429:
                logger.info("chrome translate rate-limited")
                return None
            response.raise_for_status()
            return _parse_payload(response.json())
    except Exception as exc:  # noqa: BLE001
        logger.info("chrome translate failed: %s", exc)
        return None


def _parse_payload(payload) -> str | None:
    return _join_segments(payload)


def _first_text(strings: list[str]) -> str | None:
    bits = [item.strip() for item in strings if item and item.strip()]
    if not bits:
        return None
    if len(bits) > 1 and all(LANG_CODE.match(item) for item in bits[1:]):
        return bits[0]
    return "".join(bits).strip() or None


def _join_segments(payload) -> str | None:
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if isinstance(first, list):
        bits: list[str] = []
        for segment in first:
            if isinstance(segment, list) and segment and isinstance(segment[0], str):
                bits.append(segment[0])
        if bits:
            return "".join(bits).strip() or None
        strings = [item for item in first if isinstance(item, str)]
        return _first_text(strings)
    if isinstance(first, str):
        strings = [item for item in payload if isinstance(item, str)]
        return _first_text(strings)
    return None


def _unpack_story(english: str, original_title: str, original_excerpt: str) -> tuple[str, str] | None:
    text = (english or "").strip()
    if not text:
        return None
    if BODY_MARK in text:
        head, body = text.split(BODY_MARK, 1)
        title = head.replace(TITLE_MARK, "").strip()
        excerpt = body.strip()
        if title:
            return title, excerpt
    if TITLE_MARK in text:
        title = text.replace(TITLE_MARK, "").strip()
        if title:
            return title, original_excerpt
    return None
