"""Find in what you keep: the index follows the tables by itself, and answers only with what you may read."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import search
from app.main import app


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def _pair() -> tuple[TestClient, TestClient]:
    a, b = TestClient(app), TestClient(app)
    _login(a, "chris", "pass1")
    _login(b, "friend", "pass2")
    return a, b


def _topic(client: TestClient, title: str = "Letters", prompt: str = "") -> int:
    created = client.post("/topics", data={"title": title, "prompt": prompt}, follow_redirects=False)
    return int(created.headers["location"].rsplit("/", 1)[-1])


def _shared_topic(a: TestClient, b: TestClient, **kwargs) -> int:
    topic_id = _topic(a, **kwargs)
    a.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    b.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    return topic_id


def _writing(client: TestClient, topic_id: int, body: str, title: str = "") -> int:
    r = client.post(f"/topics/{topic_id}/writings", data={"title": title, "body": body}, follow_redirects=False)
    return int(r.headers["location"].rsplit("-", 1)[-1])


def _index(db: Session) -> set[tuple[str, int]]:
    return {(k, int(i)) for k, i in db.execute(text("SELECT kind, object_id FROM search_index")).all()}


def _hrefs(client: TestClient, q: str) -> list[str]:
    page = client.get("/search", params={"q": q}).text
    return [part.split('"')[0] for part in page.split('<a class="hit" href="')[1:]]


# --- the expression and the excerpt ---------------------------------------------------------


def test_anything_typed_becomes_a_safe_prefix_query():
    assert search.fts_query("river") == '"river"*'
    assert search.fts_query('  the "quiet" room  ') == '"the"* "quiet"* "room"*'
    # operators and column filters become plain terms; "z^2" splits in two and the eighth term is the cap
    assert search.fts_query("NOT (x OR y) AND z^2 col:val") == '"NOT"* "x"* "OR"* "y"* "AND"* "z"* "2"* "col"*'
    assert search.fts_query("héllo — wörld") == '"héllo"* "wörld"*'
    assert search.fts_query("- '' ’") == ""
    assert search.fts_query("") == ""
    assert len(search.fts_query(" ".join(f"w{i}" for i in range(40))).split()) == search.MAX_TERMS


def test_excerpt_escapes_html_and_marks_the_match():
    out = search.excerpt("a <b>bold</b> \x01river\x02 & **more** `code`\n\n# heading")
    assert str(out) == "a &lt;b&gt;bold&lt;/b&gt; <mark>river</mark> &amp; more code heading"


# --- the index follows the tables -------------------------------------------------------------


def test_index_follows_insert_edit_and_delete(client: TestClient, db_session: Session):
    _login(client, "chris", "pass1")
    topic_id = _topic(client, "Orchard", prompt="about apples")
    wid = _writing(client, topic_id, "the river was low", title="Low water")
    client.post(f"/topics/{topic_id}/comments", data={"body": "a note on cider"}, follow_redirects=False)
    assert _index(db_session) >= {("topic", topic_id), ("writing", wid)}
    assert len([k for k, _ in _index(db_session) if k == "comment"]) == 1

    assert _hrefs(client, "river") == [f"/topics/{topic_id}/read#writing-{wid}"]
    edit = {"title": "Low water", "body": "the well ran dry"}
    client.post(f"/writings/{wid}/edit", data=edit, follow_redirects=False)
    assert _hrefs(client, "river") == []
    assert _hrefs(client, "well") == [f"/topics/{topic_id}/read#writing-{wid}"]
    assert _hrefs(client, "apple") == [f"/topics/{topic_id}/read"]
    assert _hrefs(client, "cider")[0].startswith(f"/topics/{topic_id}/read#note-")

    client.post(f"/topics/{topic_id}/edit", data={"title": "Orchard", "prompt": "about pears"}, follow_redirects=False)
    assert _hrefs(client, "apple") == [] and _hrefs(client, "pear") == [f"/topics/{topic_id}/read"]

    client.post(f"/writings/{wid}/delete", follow_redirects=False)
    assert ("writing", wid) not in _index(db_session)
    client.post(f"/topics/{topic_id}/delete", follow_redirects=False)
    assert _index(db_session) == set()


def test_an_accepted_revision_is_what_gets_found(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    wid = _writing(a, topic_id, "first wording")
    a.post(f"/writings/{wid}/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/accept", follow_redirects=False)
    a.post(f"/writings/{wid}/revision", data={"title": "", "body": "second wording"}, follow_redirects=False)
    assert _hrefs(b, "second") == [] and _hrefs(a, "second") == []  # a draft revision is not a page yet
    a.post(f"/writings/{wid}/revision/offer", follow_redirects=False)
    b.post(f"/writings/{wid}/revision/accept", follow_redirects=False)
    assert _hrefs(b, "second") == [f"/topics/{topic_id}/read#writing-{wid}"] and _hrefs(b, "first") == []


# --- who may find what -----------------------------------------------------------------------


def test_search_answers_with_openness_only(db_session: Session):
    a, b = _pair()
    private_topic = _topic(a, "Secret garden", prompt="hidden-prompt-word")
    hidden = _writing(a, private_topic, "hidden-page-word")
    shared = _shared_topic(a, b, title="Open field", prompt="open-prompt-word")
    sealed = _writing(a, shared, "sealed-page-word", title="sealed-title-word")
    a.post(f"/writings/{sealed}/offer", follow_redirects=False)
    opened = _writing(a, shared, "opened-page-word")
    a.post(f"/writings/{opened}/offer", follow_redirects=False)
    b.post(f"/writings/{opened}/accept", follow_redirects=False)
    a.post(f"/topics/{shared}/comments", data={"body": "private-note-word"}, follow_redirects=False)

    secrets = ("hidden-prompt-word", "hidden-page-word", "sealed-page-word", "sealed-title-word", "private-note-word")
    for word in secrets:
        assert _hrefs(b, word) == [], word
        assert "Secret garden" not in b.get("/search", params={"q": word}).text
    assert _hrefs(b, "open-prompt") == [f"/topics/{shared}/read"]
    assert _hrefs(b, "opened-page") == [f"/topics/{shared}/read#writing-{opened}"]
    assert _hrefs(a, "hidden-page") == [f"/topics/{private_topic}/read#writing-{hidden}"]

    # a withdrawn page disappears from the other person's results at once
    a.post(f"/writings/{opened}/revoke", follow_redirects=False)
    assert _hrefs(b, "opened-page") == []
    assert _hrefs(a, "opened-page") == [f"/topics/{shared}/read#writing-{opened}"]


def test_margin_lines_are_found_only_while_the_topic_is_shared(db_session: Session):
    a, b = _pair()
    topic_id = _shared_topic(a, b)
    with b.websocket_connect(f"/ws/topics/{topic_id}") as ws:
        ws.send_json({"type": "chat", "body": "a margin-only-word here"})
        ws.receive_json()
    hits = _hrefs(a, "margin-only")
    assert len(hits) == 1 and hits[0].startswith(f"/topics/{topic_id}/read#margin-")
    a.post(f"/topics/{topic_id}/revoke", follow_redirects=False)
    assert _hrefs(a, "margin-only") == [] and _hrefs(b, "margin-only") == []


def test_search_page_needs_a_person_and_shows_the_excerpt(client: TestClient):
    assert client.get("/search?q=x", follow_redirects=False).status_code == 303
    _login(client, "chris", "pass1")
    topic_id = _topic(client, "Night")
    _writing(client, topic_id, "<script>alert(1)</script> the **lantern** swung", title="Lantern")
    page = client.get("/search", params={"q": "lant"}).text
    assert "<script>" not in page
    assert "<mark>lantern</mark>" in page  # a prefix query marks the whole word it found
    assert "1 place where “lant” appears" in page
    assert 'class="find"' in client.get("/archive").text
    assert "Type a word or two." in client.get("/search", params={"q": "--"}).text
