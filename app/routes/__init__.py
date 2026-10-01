"""One router per URL space. `main.py` includes them all."""

from __future__ import annotations

from fastapi import APIRouter

from . import archive, auth, chat, comments, home, topics, writings

routers: tuple[APIRouter, ...] = (
    auth.router,
    home.router,
    topics.router,
    writings.router,
    comments.router,
    archive.router,
    chat.router,
)
