import re

from openai import OpenAI
import httpx

from app.services.settings import LlmConfig, normalize_ollama_root

SYSTEM_PROMPT = (
    "You write concise news briefings for an e-ink reader. "
    "Reply with only the summary text: 2-4 factual sentences. "
    "Do not add a preamble, greeting, title, or closing. "
    "Never start with phrases like 'Here is', 'Here's', 'Sure', "
    "'Based on the provided text', or 'A concise summary'. "
    "No hype, no clickbait. Mention the outlet only if it adds context. "
    "Do not invent facts that are not in the source text."
)

_PREAMBLE_RE = re.compile(
    r"""
    ^\s*[\"'“”`]?
    (?:(?:sure|okay|ok|certainly|of\ course)[,!]?\s+)?
    (?:
        here(?:'s|\s+is)\s+(?:a\s+)?(?:concise\s+)?(?:news\s+)?(?:briefing|summary|overview|recap)
            (?:\s+based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)?)?
        |here(?:'s|\s+is)\s+(?:the\s+)?(?:briefing|summary|overview|recap)
        |based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)
        |(?:a\s+)?concise\s+(?:news\s+)?(?:briefing|summary)
            (?:\s+based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)?)?
        |i(?:'ve|\s+have)\s+(?:written|prepared|created)\s+(?:a\s+)?(?:briefing|summary)
    )
    [^\n:]{0,80}
    [:\-–—]\s*
    [\"'“”`]?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WRAPPER_RE = re.compile(r'^[\"“”\'`]+|[\"“”\'`]+$')


def clean_summary(text: str, *, title: str = "", excerpt: str = "") -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return fallback_summary(title, excerpt)

    if len(cleaned) >= 2 and cleaned[0] in "\"“'`" and cleaned[-1] in "\"”'`":
        cleaned = cleaned[1:-1].strip()

    for _ in range(3):
        next_text = _PREAMBLE_RE.sub("", cleaned, count=1).strip()
        next_text = _WRAPPER_RE.sub("", next_text).strip()
        if next_text == cleaned:
            break
        cleaned = next_text

    lowered = cleaned.lower()
    if not cleaned or lowered.startswith(("here is", "here's", "based on the provided")):
        return fallback_summary(title, excerpt)
    return cleaned


def fallback_summary(title: str, excerpt: str) -> str:
    text = (excerpt or "").strip()
    if not text:
        return title.strip()
    compact = " ".join(text.split())
    if len(compact) <= 420:
        return compact
    clipped = compact[:417].rsplit(" ", 1)[0]
    return clipped + "…"


def summarize_story(
    title: str,
    excerpt: str,
    source: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    db=None,
) -> str:
    if not api_key or not model:
        return fallback_summary(title, excerpt)

    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    user = (
        f"Outlet: {source}\n"
        f"Headline: {title}\n\n"
        f"Source text:\n{(excerpt or title)[:4000]}\n\n"
        "Write only the summary sentences. No introduction."
    )
    try:
        response = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            temperature=0.2,
            max_tokens=220,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        from app.services.importance import record_ai_error

        record_ai_error(db, f"Summarise failed: {exc}")
        raise
    text = (response.choices[0].message.content or "").strip()
    cleaned = clean_summary(text, title=title, excerpt=excerpt)
    from app.services.importance import clear_ai_error

    clear_ai_error(db)
    return cleaned or fallback_summary(title, excerpt)


def summarize_with_config(title: str, excerpt: str, source: str, config: LlmConfig, db=None) -> str:
    if not config.ready:
        return fallback_summary(title, excerpt)
    return summarize_story(
        title,
        excerpt,
        source,
        config.api_key,
        config.model,
        base_url=config.base_url,
        db=db,
    )


def list_ollama_models(base_url: str) -> list[str]:
    root = normalize_ollama_root(base_url)
    if not root.startswith(("http://", "https://")):
        raise ValueError("Ollama URL must start with http:// or https://")
    with httpx.Client(timeout=5.0) as client:
        response = client.get(f"{root}/api/tags")
        response.raise_for_status()
        data = response.json()
    names: list[str] = []
    for item in data.get("models") or []:
        name = item.get("name") or item.get("model")
        if name:
            names.append(str(name))
    return sorted(set(names), key=str.lower)
