from __future__ import annotations

import contextlib
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket

from . import auth

MAX_SEATS_PER_PERSON = 4
FLOOD_WINDOW_SECONDS = 5.0
FLOOD_MAX_MESSAGES = 10
CLOSE_ROOM_CLOSED = 4404
CLOSE_TOO_MANY = 4429


class Seat:
    def __init__(self, ws: WebSocket, user: str):
        self.ws = ws
        self.user = user
        self.typing = False
        self.recent: deque[float] = deque()

    def allow_frame(self) -> bool:
        now = time.monotonic()
        while self.recent and now - self.recent[0] > FLOOD_WINDOW_SECONDS:
            self.recent.popleft()
        if len(self.recent) >= FLOOD_MAX_MESSAGES:
            return False
        self.recent.append(now)
        return True


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[int, dict[int, Seat]] = defaultdict(dict)

    def seats_for(self, topic_id: int, user: str) -> int:
        return sum(1 for seat in self.rooms.get(topic_id, {}).values() if seat.user == user)

    async def join(self, topic_id: int, ws: WebSocket, user: str) -> bool:
        if self.seats_for(topic_id, user) >= MAX_SEATS_PER_PERSON:
            await ws.close(code=CLOSE_TOO_MANY)
            return False
        await ws.accept()
        self.rooms[topic_id][id(ws)] = Seat(ws, user)
        await self.broadcast_presence(topic_id)
        return True

    def leave(self, topic_id: int, ws: WebSocket) -> None:
        room = self.rooms.get(topic_id)
        if room is None:
            return
        room.pop(id(ws), None)
        if not room:
            self.rooms.pop(topic_id, None)

    def seat(self, topic_id: int, ws: WebSocket) -> Seat | None:
        return self.rooms.get(topic_id, {}).get(id(ws))

    def set_typing(self, topic_id: int, ws: WebSocket, typing: bool) -> None:
        seat = self.seat(topic_id, ws)
        if seat:
            seat.typing = typing

    def presence(self, topic_id: int) -> dict[str, Any]:
        seats = list(self.rooms.get(topic_id, {}).values())
        seen: dict[str, bool] = {}
        for seat in seats:
            seen[seat.user] = seen.get(seat.user, False) or seat.typing
        return {
            "type": "presence",
            "here": [{"user": name, "display": auth.display_for(name)} for name in seen],
            "typing": [auth.display_for(name) for name, flag in seen.items() if flag],
        }

    async def broadcast(self, topic_id: int, payload: dict[str, Any]) -> None:
        dead: list[int] = []
        for key, seat in list(self.rooms.get(topic_id, {}).items()):
            try:
                await seat.ws.send_json(payload)
            except Exception:
                dead.append(key)
        room = self.rooms.get(topic_id)
        if room is not None:
            for key in dead:
                room.pop(key, None)
            if not room:
                self.rooms.pop(topic_id, None)

    async def broadcast_presence(self, topic_id: int) -> None:
        await self.broadcast(topic_id, self.presence(topic_id))

    async def close_room(self, topic_id: int) -> None:
        """The topic left the table: everyone is shown out, at once."""
        room = self.rooms.pop(topic_id, None)
        if not room:
            return
        for seat in room.values():
            with contextlib.suppress(Exception):
                await seat.ws.close(code=CLOSE_ROOM_CLOSED)


hub = Hub()
