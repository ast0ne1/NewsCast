from __future__ import annotations

import re
from datetime import datetime, timezone

from openai import OpenAI
from sqlalchemy.orm import Session

from app.services.settings import LlmConfig, get_value, set_value

IMPORTANCE_MIN = 1
IMPORTANCE_MAX = 5
DEFAULT_MIN_IMPORTANCE = 3

_MAJOR_RE = re.compile(
    r"\b("
    r"breaking|just\s+in|developing|exclusive|"
    r"war|invasion|missile|nuclear|ceasefire|coup|"
    r"earthquake|hurricane|wildfire|pandemic|"
    r"assassination|mass\s+shooting|hostage|"
    r"election|landslide|impeach|"
    r"bankrupt(?:cy|s)?|collapse|crash|recession|"
    r"dies|dead|killed|fatal|"
    r"announces|unveils|launches"
    r")\b",
    re.I,
)
_BOOST_RE = re.compile(
    r"\b("
    r"apple|google|microsoft|amazon|meta|tesla|openai|"
    r"nato|un\b|eu\b|white\s+house|parliament|supreme\s+court|"
    r"olympics|world\s+cup|championship"
    r")\b",
    re.I,
)
_SOFT_RE = re.compile(
    r"\b("
    r"tips?|how\s+to|recipe|review|opinion|column|"
    r"gossip|rumou?r|celebrity|horoscope|quiz|"
    r"sponsored|advertorial|deal\s+of\s+the\s+day"
    r")\b",
    re.I,
)
_CATEGORY_BASE = {
    "news": 3,
    "world": 3,
    "business": 3,
    "technology": 3,
    "science": 3,
    "sport": 2,
    "culture": 2,
    "longreads": 3,
}


def clamp_importance(value: int | float | None, default: int = 3) -> int:
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        score = default
    return max(IMPORTANCE_MIN, min(IMPORTANCE_MAX, score))


def record_ai_error(db: Session | None, message: str) -> None:
    if db is None:
        return
    text = (message or "").strip()[:500]
    if not text:
        return
    set_value(db, "last_ai_error", text)
    set_value(db, "last_ai_error_at", datetime.now(timezone.utc).replace(microsecond=0).isoformat())


def clear_ai_error(db: Session | None) -> None:
    if db is None:
        return
    if get_value(db, "last_ai_error"):
        set_value(db, "last_ai_error", "")
        set_value(db, "last_ai_error_at", "")


def heuristic_importance(
    title: str,
    excerpt: str = "",
    *,
    category: str = "news",
    source: str = "",
) -> int:
    text = f"{title or ''}\n{excerpt or ''}"
    score = _CATEGORY_BASE.get((category or "news").lower(), 3)
    if _MAJOR_RE.search(title or "") or _MAJOR_RE.search(text[:400]):
        score += 1
    if _BOOST_RE.search(title or ""):
        score += 1
    if _SOFT_RE.search(title or "") or _SOFT_RE.search(text[:300]):
        score -= 1
    compact = " ".join((excerpt or "").split())
    if len(compact) < 40 and not _MAJOR_RE.search(title or ""):
        score -= 1
    if source and source.lower() in {"reuters", "ap", "associated press", "bbc world", "bbc news"}:
        score += 0  # kept for future outlet weighting
    return clamp_importance(score)


def _llm_importance(title: str, excerpt: str, source: str, config: LlmConfig) -> int | None:
    if not config.ready:
        return None
    client = OpenAI(api_key=config.api_key, base_url=config.base_url) if config.base_url else OpenAI(api_key=config.api_key)
    user = (
        f"Outlet: {source}\n"
        f"Headline: {title}\n\n"
        f"Excerpt:\n{(excerpt or title)[:2000]}\n\n"
        "Rate how important this story is for a daily newspaper briefing.\n"
        "1=ignore, 2=interesting, 3=worth reading, 4=important, 5=major story.\n"
        "Reply with only the integer."
    )
    response = client.chat.completions.create(
        model=config.model or "gpt-4o-mini",
        temperature=0,
        max_tokens=8,
        messages=[
            {
                "role": "system",
                "content": "You score news importance. Reply with only an integer from 1 to 5.",
            },
            {"role": "user", "content": user},
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    match = re.search(r"[1-5]", text)
    if not match:
        return None
    return clamp_importance(int(match.group(0)))


def score_importance(
    title: str,
    excerpt: str = "",
    *,
    source: str = "",
    category: str = "news",
    config: LlmConfig | None = None,
    db: Session | None = None,
) -> int:
    fallback = heuristic_importance(title, excerpt, category=category, source=source)
    if config is None or not config.ready:
        return fallback
    try:
        scored = _llm_importance(title, excerpt, source, config)
    except Exception as exc:  # noqa: BLE001
        record_ai_error(db, f"Importance scoring failed: {exc}")
        return fallback
    if scored is None:
        record_ai_error(db, "Importance scoring returned no score.")
        return fallback
    clear_ai_error(db)
    return scored


def effective_importance(story, *, category: str = "news") -> int:
    raw = getattr(story, "importance", None)
    if raw is not None:
        return clamp_importance(raw)
    return heuristic_importance(
        getattr(story, "title", "") or "",
        getattr(story, "raw_excerpt", None) or getattr(story, "summary", "") or "",
        category=category,
        source=getattr(story, "source_name", "") or "",
    )
