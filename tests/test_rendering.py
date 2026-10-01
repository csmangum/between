"""The one Markdown path: expressive for letters, and the allowlist widened only for what it emits."""

from __future__ import annotations

import re

from app.markdown_render import _render_cached, render_markdown


def test_smart_typography():
    html = render_markdown('"Quoted" -- a dash --- and then...')
    assert "“Quoted”" in html
    assert "–" in html and "—" in html and "…" in html


def test_code_is_left_alone_by_smart_typography():
    html = render_markdown('Use `"--"` here and `...` there')
    assert '<code>"--"</code>' in html
    assert "<code>...</code>" in html


def test_footnotes_render_with_page_unique_ids():
    first = render_markdown("Claim.[^1]\n\n[^1]: Note *here*.")
    second = render_markdown("Other claim.[^1]\n\n[^1]: Another note.")
    assert '<div class="footnote">' in first
    assert '<a class="footnote-ref" href="#fn:' in first
    assert "<em>here</em>" in first
    ids_first = set(re.findall(r'id="(fn(?:ref)?:[^"]+)"', first))
    ids_second = set(re.findall(r'id="(fn(?:ref)?:[^"]+)"', second))
    assert ids_first and ids_second
    assert ids_first.isdisjoint(ids_second), "two writings on one page must not share footnote anchors"
    # Internal anchors do not get the external-link treatment.
    assert 'target="_blank"' not in first


def test_definition_lists_and_abbreviations():
    html = render_markdown("Letter\n:   A page sent and then opened.\n\nThe W3C.\n\n*[W3C]: World Wide Web Consortium")
    assert "<dl>" in html and "<dt>Letter</dt>" in html and "<dd>A page sent and then opened.</dd>" in html
    assert '<abbr title="World Wide Web Consortium">W3C</abbr>' in html


def test_hand_written_classes_and_ids_are_dropped():
    html = render_markdown(
        '<div class="panel" id="content">x</div> <sup id="hijack">1</sup> '
        '<a class="btn" href="https://e.org">l</a> <abbr title="t" class="x">a</abbr> H<sub>2</sub>O'
    )
    assert "<div>x</div>" in html
    assert "<sup>1</sup>" in html
    assert 'class="btn"' not in html and 'id="content"' not in html and 'class="x"' not in html
    assert '<a href="https://e.org" rel="noopener noreferrer" target="_blank">l</a>' in html
    assert "H<sub>2</sub>O" in html


def test_scripts_images_and_bad_schemes_still_stripped():
    html = render_markdown('<script>alert(1)</script><img src="http://t/p.gif" alt="pic"> [x](javascript:alert(1))')
    assert "<script" not in html and "<img" not in html
    assert "pic" in html
    assert "javascript:" not in html


def test_preview_renders_do_not_enter_the_cache():
    _render_cached.cache_clear()
    render_markdown("kept page", cache=True)
    before = _render_cached.cache_info().currsize
    render_markdown("still typing", cache=False)
    render_markdown("still typin", cache=False)
    assert _render_cached.cache_info().currsize == before


# --- the preview endpoint ----------------------------------------------------------------------


def test_preview_requires_a_session(client):
    response = client.post("/preview", data={"body": "**x**"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_preview_uses_the_same_sanitized_renderer(client):
    client.post("/login", data={"username": "chris", "password": "pass1"}, follow_redirects=False)
    _render_cached.cache_clear()
    response = client.post("/preview", data={"body": '"Hi" <script>1</script>[^1]\n\n[^1]: note'})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "“Hi”" in response.text and "<script" not in response.text
    assert 'class="footnote"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert _render_cached.cache_info().currsize == 0, "previews must not fill the page cache"


def test_preview_refuses_absurd_lengths(client):
    client.post("/login", data={"username": "chris", "password": "pass1"}, follow_redirects=False)
    response = client.post("/preview", data={"body": "x" * 200_001})
    assert response.status_code == 413
