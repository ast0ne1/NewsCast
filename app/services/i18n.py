"""UI localisation — English msgid keys, JSON catalogs under app/locales/."""

from __future__ import annotations

import json

from app.config import ROOT_DIR

LOCALES_DIR = ROOT_DIR / "app" / "locales"
DEFAULT_LANG = "en"

# Slim set passed to window.NEWSCAST_I18N for client-only strings.
JS_KEYS = (
    "Cancel",
    "Remove",
    "Checking…",
    "Adding…",
    "Save settings",
    "Idle",
    "Refreshing",
    "Error",
)

_catalog_cache: dict[str, dict[str, str]] = {}


def clear_cache() -> None:
    _catalog_cache.clear()


def load_catalog(lang: str) -> dict[str, str]:
    code = (lang or DEFAULT_LANG).strip().lower() or DEFAULT_LANG
    cached = _catalog_cache.get(code)
    if cached is not None:
        return cached
    path = LOCALES_DIR / f"{code}.json"
    data: dict[str, str] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = {}
    _catalog_cache[code] = data
    return data


def t(key: str, *, lang: str = DEFAULT_LANG, **kwargs: object) -> str:
    """Translate msgid `key` for `lang`, falling back to English then the key."""
    msgid = key if isinstance(key, str) else str(key)
    code = (lang or DEFAULT_LANG).strip().lower() or DEFAULT_LANG
    catalog = load_catalog(code)
    text = catalog.get(msgid)
    if text is None and code != DEFAULT_LANG:
        text = load_catalog(DEFAULT_LANG).get(msgid)
    if text is None:
        text = msgid
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text


def js_bundle(lang: str = DEFAULT_LANG) -> dict[str, str]:
    """Small dict of strings for window.NEWSCAST_I18N."""
    return {key: t(key, lang=lang) for key in JS_KEYS}


def make_t(lang: str):
    code = (lang or DEFAULT_LANG).strip().lower() or DEFAULT_LANG

    def _translate(key: str, **kwargs: object) -> str:
        return t(key, lang=code, **kwargs)

    return _translate
