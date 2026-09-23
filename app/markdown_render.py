from __future__ import annotations

import hashlib
from functools import lru_cache

import bleach
import markdown

ALLOWED_TAGS = bleach.sanitizer.ALLOWED_TAGS.union(
    {
        "p",
        "pre",
        "code",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
        "hr",
        "ul",
        "ol",
        "li",
        "em",
        "strong",
        "a",
        "br",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
)
ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
}


@lru_cache(maxsize=512)
def _render_cached(digest: str, text: str) -> str:
    raw = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return bleach.clean(raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)


def render_markdown(text: str | None) -> str:
    body = text or ""
    digest = hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()
    return _render_cached(digest, body)


def clear_markdown_cache() -> None:
    _render_cached.cache_clear()
