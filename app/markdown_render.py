from __future__ import annotations

import hashlib
import re
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
    "a": ["href", "title", "rel", "target"],
}

_ANCHOR = re.compile(r"<a\s+([^>]+)>", re.IGNORECASE)


def _annotate_links(html: str) -> str:
    """External links leave the room in a new tab and do not leak the opener."""

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1)
        href_match = re.search(r'href="([^"]*)"', attrs)
        if not href_match:
            return match.group(0)
        href = href_match.group(1)
        if not href.startswith(("http://", "https://", "//")):
            return match.group(0)
        if "rel=" not in attrs:
            attrs += ' rel="noopener noreferrer"'
        if "target=" not in attrs:
            attrs += ' target="_blank"'
        return f"<a {attrs}>"

    return _ANCHOR.sub(repl, html)


@lru_cache(maxsize=512)
def _render_cached(digest: str, text: str) -> str:
    raw = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    cleaned = bleach.clean(raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
    return _annotate_links(cleaned)


def render_markdown(text: str | None) -> str:
    body = text or ""
    digest = hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()
    return _render_cached(digest, body)


def clear_markdown_cache() -> None:
    _render_cached.cache_clear()
