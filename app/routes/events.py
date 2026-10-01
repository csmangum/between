"""GET /events: the signed-in person's own stream, as text/event-stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..events import Payload, stream
from ..queries import sealed_offer_count
from ..views import current_user

router = APIRouter()

KEEPALIVE_SECONDS = 25
RETRY_MS = 3000


def frame(payload: Payload) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def frames(user: str) -> AsyncIterator[str]:
    listener = stream.subscribe(user)
    try:
        sealed = await run_in_threadpool(sealed_offer_count, user)
        yield f"retry: {RETRY_MS}\n" + frame({"type": "hello", "sealed": sealed})
        while True:
            try:
                payload = await asyncio.wait_for(listener.queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": still here\n\n"
                continue
            if payload is None:
                return
            yield frame(payload)
    finally:
        stream.unsubscribe(listener)


@router.get("/events")
async def events(request: Request) -> Response:
    user = current_user(request)
    if not user:
        # Not a redirect: EventSource gives up on a non-200 instead of retrying into the login page.
        return Response(status_code=401)
    return StreamingResponse(frames(user), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
