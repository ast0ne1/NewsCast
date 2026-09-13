from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
}


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    clean = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower().removeprefix("www."),
        query=urlencode(query, doseq=True),
        fragment="",
    )
    return urlunparse(clean).rstrip("/")


def normalize_title(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_tokens(title: str) -> set[str]:
    return {token for token in normalize_title(title).split() if len(token) > 2}


def token_jaccard(left: str, right: str) -> float:
    a = title_tokens(left)
    b = title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(left: str, right: str) -> float:
    jaccard = token_jaccard(left, right)
    sequence = SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()
    return max(jaccard, sequence)


def content_hash(title: str, excerpt: str) -> str:
    payload = f"{normalize_title(title)}\n{(excerpt or '')[:500].lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cluster_key(title: str) -> str:
    tokens = list(title_tokens(title))[:8]
    return " ".join(sorted(tokens)) or normalize_title(title)[:80]


def is_duplicate_title(candidate: str, existing: str, threshold: float = 0.72) -> bool:
    return title_similarity(candidate, existing) >= threshold
