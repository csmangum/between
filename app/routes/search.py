"""GET /search: find a word in what you keep. Answers only with what the archive would show."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..search import MAX_QUERY_CHARS, fts_query, search
from ..views import render, require_user

router = APIRouter()


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = Query(""), db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    query = q.strip()[:MAX_QUERY_CHARS]
    asked = bool(fts_query(query))
    hits = search(db, user, query) if asked else []
    return render(request, "search.html", db=db, q=query, asked=asked, hits=hits)
