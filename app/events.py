"""One quiet stream per person.

Open question 6 asked whether notifications would turn the room into a messenger. The answer here is the
smallest thing that keeps the single badge on home truthful: a server-sent stream that carries the sealed
count and, when the other person acts on something, which topic it was and what happened. Nothing about the
margin, no history, no sound. The browser updates the badge and, if the person is looking at that very page,
shows one line.

Like the chat hub, this lives in the process: run one worker.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any, Literal

from sqlalchemy.orm import Session

from . import access
from .queries import sealed_offer_count

What = Literal["sealed", "opened", "unopened", "returned"]
Payload = dict[str, Any]

MAX_LISTENERS_PER_PERSON = 6


class Listener:
    def __init__(self, user: str) -> None:
        self.user = user
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[Payload | None] = asyncio.Queue()

    def deliver(self, payload: Payload | None) -> None:
        """Safe from any thread: most routes run in the threadpool, the loop owns the queue."""
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)


class Stream:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[str, list[Listener]] = defaultdict(list)

    def subscribe(self, user: str) -> Listener:
        listener = Listener(user)
        with self._lock:
            seats = self._listeners[user]
            seats.append(listener)
            evicted = seats[:-MAX_LISTENERS_PER_PERSON]
            del seats[:-MAX_LISTENERS_PER_PERSON]
        for old in evicted:
            old.deliver({"type": "close", "reason": "over_limit"})
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            seats = self._listeners.get(listener.user)
            if seats and listener in seats:
                seats.remove(listener)
            if not seats:
                self._listeners.pop(listener.user, None)

    def listening(self, user: str) -> int:
        with self._lock:
            return len(self._listeners.get(user, ()))

    def publish(self, user: str, payload: Payload) -> None:
        with self._lock:
            targets = list(self._listeners.get(user, ()))
        for listener in targets:
            listener.deliver(payload)

    def close_all(self) -> None:
        """Shutdown, or a test: every open stream ends after its next read."""
        with self._lock:
            targets = [listener for seats in self._listeners.values() for listener in seats]
            self._listeners.clear()
        for listener in targets:
            listener.deliver(None)


stream = Stream()


def tell(db: Session, actor: str, what: What, topic_id: int) -> None:
    """The other person did something to a topic this person can see. Call it after the commit."""
    other = access.other_username(actor)
    if not other:
        return
    stream.publish(other, {"type": "topic", "what": what, "topic": topic_id, "sealed": sealed_offer_count(other, db)})
