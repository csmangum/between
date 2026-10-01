"""One router per URL space. `main.py` includes them all."""

from __future__ import annotations

from fastapi import APIRouter

from . import archive, auth, chat, comments, events, home, preview, reading, search, topics, writings

routers: tuple[APIRouter, ...] = (
    auth.router,
    home.router,
    topics.router,
    reading.router,
    writings.router,
    comments.router,
    preview.router,
    archive.router,
    search.router,
    chat.router,
    events.router,
)
