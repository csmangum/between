"""Live preview for the desk: the same renderer and sanitizer the kept page will use, nothing stored."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from ..markdown_render import render_markdown
from ..views import require_user

router = APIRouter()

PREVIEW_MAX_CHARS = 200_000


@router.post("/preview", response_class=HTMLResponse)
def preview(request: Request, body: str = Form("")) -> Response:
    require_user(request)
    if len(body) > PREVIEW_MAX_CHARS:
        return Response("Too long to preview.", status_code=413, media_type="text/plain")
    return HTMLResponse(render_markdown(body, cache=False))
