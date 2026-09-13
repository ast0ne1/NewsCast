from openai import OpenAI
import httpx

from app.services.settings import LlmConfig, normalize_ollama_root

SYSTEM_PROMPT = (
    "You write concise news briefings for an e-ink reader. "
    "Summarize the story in 2-4 factual sentences. No hype, no clickbait, "
    "no preamble. Mention the outlet only if it adds context. "
    "Do not invent facts that are not in the source text."
)


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
) -> str:
    if not api_key or not model:
        return fallback_summary(title, excerpt)

    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    user = (
        f"Outlet: {source}\n"
        f"Headline: {title}\n\n"
        f"Source text:\n{(excerpt or title)[:4000]}"
    )
    response = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        temperature=0.2,
        max_tokens=220,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    return text or fallback_summary(title, excerpt)


def summarize_with_config(title: str, excerpt: str, source: str, config: LlmConfig) -> str:
    if not config.ready:
        return fallback_summary(title, excerpt)
    return summarize_story(
        title,
        excerpt,
        source,
        config.api_key,
        config.model,
        base_url=config.base_url,
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
