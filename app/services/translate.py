from __future__ import annotations

import logging
import re
import time

import httpx
from openai import OpenAI
from sqlalchemy.orm import Session

from app.services.settings import LlmConfig

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
PROVIDERS = ("google", "llm")
FEED_PROVIDER_CHOICES = (
    ("off", "As published"),
    ("global", "Translate (use global setting)"),
    ("google", "Translate with Google"),
    ("llm", "Translate with LLM"),
)
TARGET_LANGUAGES = (
    ("en", "English"),
    ("es", "Spanish"),
    ("de", "German"),
    ("fr", "French"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("fi", "Finnish"),
    ("pl", "Polish"),
    ("ro", "Romanian"),
)
TARGET_LANG_IDS = {code for code, _label in TARGET_LANGUAGES}
TARGET_LANG_LABELS = {code: label for code, label in TARGET_LANGUAGES}
DEFAULT_TARGET_LANG = "en"


def normalize_target_lang(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in TARGET_LANG_IDS else DEFAULT_TARGET_LANG


def target_language_name(code: str | None) -> str:
    return TARGET_LANG_LABELS.get(normalize_target_lang(code), "English")


def translate_target_lang(db: Session | None) -> str:
    from app.services import settings as settings_service

    if db is None:
        return DEFAULT_TARGET_LANG
    return settings_service.translate_target_lang(db)


def needs_translation(story, target_lang: str) -> bool:
    stored = (getattr(story, "content_lang", None) or "").strip().lower()
    return stored != normalize_target_lang(target_lang)


def llm_system_prompt(target_lang: str) -> str:
    name = target_language_name(target_lang)
    return (
        f"You translate news headlines and article excerpts into clear {name}. "
        f"Reply with only the {name} translation. Keep the meaning. "
        "Do not summarise, explain, or add notes."
    )


def looks_untranslated(text: str) -> bool:
    if not text:
        return False
    if NORDIC_WORDS.search(text):
        return True
    # Nordic letters in names (Bælt, Søe) are fine once the sentence is English.
    return bool(NORDIC_LETTERS.search(text) and not ENGLISH_HINT.search(text))


def normalize_provider(value: str | None, *, default: str = "google") -> str:
    key = (value or "").strip().lower()
    return key if key in PROVIDERS else default


def parse_feed_translate_mode(value: str | None) -> tuple[bool, str]:
    mode = (value or "off").strip().lower()
    if mode in {"1", "true", "yes", "global", "translate"}:
        return True, "global"
    if mode == "google":
        return True, "google"
    if mode == "llm":
        return True, "llm"
    return False, "global"


def feed_translate_mode(feed) -> str:
    if not bool(getattr(feed, "translate", False)):
        return "off"
    raw = (getattr(feed, "translate_provider", None) or "global").strip().lower()
    if raw in PROVIDERS:
        return raw
    return "global"


def resolve_provider(db: Session | None, feed=None, *, global_provider: str | None = None) -> str | None:
    from app.services import settings as settings_service

    if feed is not None and not bool(getattr(feed, "translate", False)):
        return None
    feed_raw = (getattr(feed, "translate_provider", None) or "global").strip().lower() if feed is not None else "global"
    if feed_raw in PROVIDERS:
        return feed_raw
    if global_provider:
        return normalize_provider(global_provider)
    if db is None:
        return "google"
    return settings_service.translate_provider(db)


def translate_text(
    text: str,
    *,
    provider: str = "google",
    target_lang: str = DEFAULT_TARGET_LANG,
    config: LlmConfig | None = None,
    db: Session | None = None,
) -> str:
    source = (text or "").strip()
    if not source:
        return ""
    engine = normalize_provider(provider)
    target = normalize_target_lang(target_lang)
    if engine == "llm":
        translated = _translate_with_llm(source, target_lang=target, config=config, db=db)
        return translated if translated else source
    parts = [_translate_chunk_google(chunk, target_lang=target) for chunk in _chunks(source)]
    if any(part is None for part in parts):
        return source
    return " ".join(part.strip() for part in parts if part).strip() or source


def translate_to_english(
    text: str,
    *,
    provider: str = "google",
    config: LlmConfig | None = None,
    db: Session | None = None,
) -> str:
    return translate_text(text, provider=provider, target_lang="en", config=config, db=db)


def translate_story(
    title: str,
    excerpt: str,
    *,
    provider: str = "google",
    target_lang: str = DEFAULT_TARGET_LANG,
    config: LlmConfig | None = None,
    db: Session | None = None,
) -> tuple[str, str]:
    title = (title or "").strip()
    excerpt = (excerpt or "").strip()
    engine = normalize_provider(provider)
    target = normalize_target_lang(target_lang)
    if excerpt:
        packed = f"{TITLE_MARK}\n{title}\n{BODY_MARK}\n{excerpt}"
        rendered = translate_text(packed, provider=engine, target_lang=target, config=config, db=db)
        parsed = _unpack_story(rendered, title, excerpt)
        if parsed:
            return parsed
    new_title = translate_text(title, provider=engine, target_lang=target, config=config, db=db) or title
    new_excerpt = (
        translate_text(excerpt, provider=engine, target_lang=target, config=config, db=db) if excerpt else ""
    )
    return new_title.strip() or title, new_excerpt


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


def _translate_chunk_google(text: str, *, target_lang: str = DEFAULT_TARGET_LANG) -> str | None:
    target = normalize_target_lang(target_lang)
    for attempt, requester in enumerate((_post_gtx, _get_chrome)):
        result = requester(text, target_lang=target)
        if result:
            return result
        time.sleep(0.6 * (attempt + 1))
    logger.warning("translate failed after Google fallbacks")
    return None


def _translate_with_llm(
    text: str,
    *,
    target_lang: str = DEFAULT_TARGET_LANG,
    config: LlmConfig | None,
    db: Session | None,
) -> str | None:
    from app.services.importance import clear_ai_error, record_ai_error
    from app.services import settings as settings_service

    cfg = config
    if cfg is None and db is not None:
        cfg = settings_service.llm_config(db)
    if cfg is None or not cfg.ready:
        logger.info("llm translate skipped: model not ready")
        return None
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url) if cfg.base_url else OpenAI(api_key=cfg.api_key)
    try:
        response = client.chat.completions.create(
            model=cfg.model or "gpt-4o-mini",
            temperature=0.1,
            max_tokens=1200,
            messages=[
                {"role": "system", "content": llm_system_prompt(target_lang)},
                {"role": "user", "content": text[:8000]},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        record_ai_error(db, f"Translate failed: {exc}")
        logger.info("llm translate failed: %s", exc)
        return None
    out = (response.choices[0].message.content or "").strip()
    if not out:
        record_ai_error(db, "Translate returned empty text.")
        return None
    clear_ai_error(db)
    return out


def _post_gtx(text: str, *, target_lang: str = DEFAULT_TARGET_LANG) -> str | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.post(
                GTX_URL,
                params={"client": "gtx", "sl": "auto", "tl": normalize_target_lang(target_lang), "dt": "t"},
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


def _get_chrome(text: str, *, target_lang: str = DEFAULT_TARGET_LANG) -> str | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.get(
                CHROME_URL,
                params={
                    "client": "dict-chrome-ex",
                    "sl": "auto",
                    "tl": normalize_target_lang(target_lang),
                    "q": text,
                },
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
