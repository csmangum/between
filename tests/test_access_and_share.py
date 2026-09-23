from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import access, share


def _topic(**kwargs):
    defaults = {
        "created_by": "chris",
        "share_status": "private",
        "offered_at": None,
        "accepted_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _writing(**kwargs):
    defaults = {
        "author": "chris",
        "share_status": "private",
        "offered_at": None,
        "accepted_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _comment(**kwargs):
    defaults = {
        "author": "chris",
        "share_status": "private",
        "offered_at": None,
        "accepted_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_share_labels_match_metaphor():
    assert share.label("private") == "on your desk"
    assert share.label("offered") == "sealed"
    assert share.label("shared") == "kept between you"


def test_share_label_rejects_unknown():
    with pytest.raises(ValueError):
        share.label("public")


def test_set_status_transitions():
    obj = _topic()
    share.set_status(obj, "offered")
    assert obj.share_status == "offered"
    assert obj.offered_at is not None
    assert obj.accepted_at is None

    share.set_status(obj, "shared")
    assert obj.share_status == "shared"
    assert obj.accepted_at is not None

    share.set_status(obj, "private")
    assert obj.share_status == "private"
    assert obj.offered_at is None
    assert obj.accepted_at is None


def test_topic_visibility_matrix():
    private = _topic(share_status="private")
    offered = _topic(share_status="offered")
    shared = _topic(share_status="shared")

    assert access.topic_visible("chris", private)
    assert not access.topic_visible("friend", private)
    assert access.topic_visible("friend", offered)
    assert not access.topic_open("friend", offered)
    assert access.topic_open("friend", shared)


def test_writing_and_comment_gates():
    writing = _writing(share_status="offered", author="chris")
    comment = _comment(share_status="offered", author="chris")

    assert access.writing_visible("friend", writing)
    assert not access.writing_open("friend", writing)
    assert access.comment_visible("friend", comment)
    assert not access.comment_open("friend", comment)

    writing.share_status = "shared"
    comment.share_status = "shared"
    assert access.writing_open("friend", writing)
    assert access.comment_open("friend", comment)
