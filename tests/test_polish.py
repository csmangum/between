from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.main import count_label, fmt_dt_soft
from app.markdown_render import render_markdown


def _login(client, username: str, password: str) -> None:
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def test_soft_time_uses_the_room_voice():
    now = datetime.now(timezone.utc)
    yesterday = (now.astimezone() - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    assert fmt_dt_soft(None) == ""
    assert fmt_dt_soft(now) == "just now"
    assert fmt_dt_soft(now - timedelta(seconds=70)) == "a minute ago"
    assert fmt_dt_soft(now - timedelta(minutes=12)) == "12 minutes ago"
    assert fmt_dt_soft(now - timedelta(minutes=70)) == "an hour ago"
    assert fmt_dt_soft(yesterday).startswith("yesterday")
    weekday = fmt_dt_soft(now - timedelta(days=3))
    assert "·" in weekday
    assert "ago" not in weekday
    assert "," in fmt_dt_soft(now - timedelta(days=12))


def test_count_label_pluralizes():
    assert count_label(1, "writing") == "1 writing"
    assert count_label(2, "writing") == "2 writings"
    assert count_label(0, "line in the margin", "lines in the margin") == "0 lines in the margin"


def test_external_markdown_links_open_away_from_the_room():
    external = render_markdown("[desk](https://example.com/a)")
    assert 'rel="noopener noreferrer"' in external
    assert 'target="_blank"' in external
    internal = render_markdown("[kept](/archive)")
    assert "noopener" not in internal
    assert "target=" not in internal


def test_failed_login_keeps_the_name(client):
    response = client.post("/login", data={"username": "chris", "password": "nope"})
    assert response.status_code == 401
    assert 'value="chris"' in response.text
    assert "nope" not in response.text
    assert "did not match" in response.text


def test_blank_topic_title_does_not_land_on_the_desk(client):
    _login(client, "chris", "pass1")
    response = client.post("/topics", data={"title": "   ", "prompt": "still private"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    home = client.get("/")
    assert home.status_code == 200
    assert "A topic needs a title" in home.text
    assert "still private" not in home.text


def test_empty_writing_warns_instead_of_saving(client):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "Letters", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    response = client.post(
        f"/topics/{topic_id}/writings",
        data={"title": "Empty", "body": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/topics/{topic_id}#write"
    page = client.get(f"/topics/{topic_id}")
    assert "A writing needs words" in page.text
    assert "Empty" not in page.text


def test_opened_topic_leaves_the_private_desk(client):
    _login(client, "chris", "pass1")
    created = client.post("/topics", data={"title": "On the table", "prompt": ""}, follow_redirects=False)
    topic_id = int(created.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/topics/{topic_id}/offer", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "friend", "pass2")
    client.post(f"/topics/{topic_id}/accept", follow_redirects=False)
    client.post("/logout", follow_redirects=False)
    _login(client, "chris", "pass1")
    home = client.get("/")
    assert f'href="/topics/{topic_id}"' not in home.text
    assert 'href="/table"' in home.text
    assert "Between you" in home.text
    assert "On the table" in home.text


def test_missing_page_for_someone_in_the_room(client):
    _login(client, "chris", "pass1")
    page = client.get("/not-a-page")
    assert page.status_code == 404
    assert "This is not on the desk" in page.text
    assert 'href="#content"' in page.text


def test_missing_page_sends_strangers_to_the_door(client):
    response = client.get("/not-a-page", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_missing_json_stays_json(client):
    response = client.get("/not-a-page", headers={"accept": "application/json"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
