"""Markdown → sanitized HTML, the one path every body on a page goes through.

Extensions are chosen for letters, not documentation: smart typography, footnotes, definition
lists and abbreviations. The sanitizer allowlist is widened only for exactly what they emit."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import markdown
import nh3
from markdown.util import HtmlStash

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
    "sup",
    "sub",
    "dl",
    "dt",
    "dd",
    "abbr",
    "div",
}
# rel and target are owned by the sanitizer and _annotate_links, never by the author.
ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title"},
    "abbr": {"title"},
    "sup": {"id"},
    "li": {"id"},
}
# The only class names that may survive are the ones the footnotes extension writes (nh3 wants
# value-restricted attributes listed here and not in ALLOWED_ATTRS).
ALLOWED_CLASS_VALUES: dict[str, dict[str, set[str]]] = {
    "div": {"class": {"footnote"}},
    "a": {"class": {"footnote-ref", "footnote-backref"}},
}
FOOTNOTE_ID = re.compile(r"^fn(ref)?:[A-Za-z0-9_.:-]+$")
ALLOWED_SCHEMES = {"http", "https", "mailto"}
LINK_REL = "noopener noreferrer"

EXTENSIONS = ["fenced_code", "tables", "nl2br", "sane_lists", "smarty", "footnotes", "def_list", "abbr"]
EXTENSION_CONFIGS: Mapping[str, Mapping[str, Any]] = {
    # Several writings share a page; every render gets its own id prefix so footnote links stay distinct.
    "footnotes": {"UNIQUE_IDS": True, "BACKLINK_TITLE": "Back to the text"},
    "smarty": {"smart_angled_quotes": False},
}

_ANCHOR = re.compile(r"<a\s+([^>]+)>", re.IGNORECASE)
_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT = re.compile(r'\balt="([^"]*)"', re.IGNORECASE)
_REL = re.compile(r'\s*rel="[^"]*"')
_RAW_HTML_TAG = re.compile(r"""<[A-Za-z][A-Za-z0-9:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>""")
_HTML_ATTRIBUTE = re.compile(r"""(?P<space>\s+)(?P<name>[^\s=/>]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s/>]+))?""")


def _strip_authored_attributes(html: str) -> str:
    """Remove author-supplied classes and IDs from raw HTML before Markdown conversion."""

    def strip_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        name_end = re.match(r"<[A-Za-z][A-Za-z0-9:-]*", tag)
        if not name_end:
            return tag
        attributes = tag[name_end.end() : -1]
        attributes = _HTML_ATTRIBUTE.sub(
            lambda attribute: "" if attribute.group("name").lower() in {"class", "id"} else attribute.group(0),
            attributes,
        )
        return f"{tag[: name_end.end()]}{attributes}>"

    return _RAW_HTML_TAG.sub(strip_tag, html)


class _AuthorHTMLStash(HtmlStash):
    def store(self, html: Any) -> str:
        if isinstance(html, str):
            html = _strip_authored_attributes(html)
        return super().store(html)


_markdown = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS)
_markdown.htmlStash = _AuthorHTMLStash()
_markdown_lock = threading.Lock()


def _keep_attribute(element: str, attribute: str, value: str) -> str | None:
    """ids are only for footnote anchors; anything an author typed by hand is dropped."""
    if attribute == "id" and not FOOTNOTE_ID.match(value):
        return None
    return value


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


def _render(text: str) -> str:
    with _markdown_lock:
        _markdown.reset()
        raw = _markdown.convert(text)
    cleaned = nh3.clean(
        _images_to_alt(raw),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        tag_attribute_values=ALLOWED_CLASS_VALUES,
        attribute_filter=_keep_attribute,
        url_schemes=ALLOWED_SCHEMES,
        link_rel=LINK_REL,
    )
    return _annotate_links(cleaned)


@lru_cache(maxsize=512)
def _render_cached(digest: str, text: str) -> str:
    return _render(text)


def render_markdown(text: str | None, *, cache: bool = True) -> str:
    """`cache=False` is for previews of text still being typed, so they do not crowd out kept pages."""
    body = text or ""
    if not cache or "[^" in body:
        return _render(body)
    digest = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
    return _render_cached(digest, body)


def clear_markdown_cache() -> None:
    """Rendered bodies stay in process memory otherwise; call after delete or revoke."""
    _render_cached.cache_clear()
