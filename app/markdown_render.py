from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import markdown
import nh3

ALLOWED_TAGS = {
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
# rel and target are owned by the sanitizer and _annotate_links, never by the author.
ALLOWED_ATTRS: dict[str, set[str]] = {"a": {"href", "title"}}
ALLOWED_SCHEMES = {"http", "https", "mailto"}
LINK_REL = "noopener noreferrer"

_ANCHOR = re.compile(r"<a\s+([^>]+)>", re.IGNORECASE)
_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT = re.compile(r'\balt="([^"]*)"', re.IGNORECASE)
_REL = re.compile(r'\s*rel="[^"]*"')


def _images_to_alt(html: str) -> str:
    """No remote images in the room (they would be tracking pixels); keep the words."""

    def repl(match: re.Match[str]) -> str:
        alt = _ALT.search(match.group(0))
        return alt.group(1) if alt else ""

    return _IMG.sub(repl, html)


def _annotate_links(html: str) -> str:
    """External links leave the room in a new tab; internal ones stay plain."""

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1)
        href_match = re.search(r'href="([^"]*)"', attrs)
        if not href_match:
            return match.group(0)
        href = href_match.group(1)
        if not href.startswith(("http://", "https://", "//")):
            return f"<a {_REL.sub('', attrs).strip()}>"
        return f'<a {attrs} target="_blank">'

    return _ANCHOR.sub(repl, html)


@lru_cache(maxsize=512)
def _render_cached(digest: str, text: str) -> str:
    raw = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    cleaned = nh3.clean(
        _images_to_alt(raw),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes=ALLOWED_SCHEMES,
        link_rel=LINK_REL,
    )
    return _annotate_links(cleaned)


def render_markdown(text: str | None) -> str:
    body = text or ""
    digest = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
    return _render_cached(digest, body)


def clear_markdown_cache() -> None:
    """Rendered bodies stay in process memory otherwise; call after delete or revoke."""
    _render_cached.cache_clear()
