from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket

from . import auth


class Seat:
    def __init__(self, ws: WebSocket, user: str):
        self.ws = ws
        self.user = user
        self.typing = False


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[int, dict[int, Seat]] = defaultdict(dict)

    async def join(self, topic_id: int, ws: WebSocket, user: str) -> None:
        await ws.accept()
        self.rooms[topic_id][id(ws)] = Seat(ws, user)
        await self.broadcast_presence(topic_id)

    def leave(self, topic_id: int, ws: WebSocket) -> None:
        self.rooms[topic_id].pop(id(ws), None)
        if not self.rooms[topic_id]:
            self.rooms.pop(topic_id, None)

    def set_typing(self, topic_id: int, ws: WebSocket, typing: bool) -> None:
        seat = self.rooms[topic_id].get(id(ws))
        if seat:
            seat.typing = typing

    def presence(self, topic_id: int) -> dict:
        seats = list(self.rooms.get(topic_id, {}).values())
        seen: dict[str, bool] = {}
        for seat in seats:
            seen[seat.user] = seen.get(seat.user, False) or seat.typing
        return {
            "type": "presence",
            "here": [{"user": name, "display": auth.display_for(name)} for name in seen],
            "typing": [auth.display_for(name) for name, flag in seen.items() if flag],
        }

    async def broadcast(self, topic_id: int, payload: dict) -> None:
        dead: list[int] = []
        for key, seat in list(self.rooms.get(topic_id, {}).items()):
            try:
                await seat.ws.send_json(payload)
            except Exception:
                dead.append(key)
        for key in dead:
            self.rooms[topic_id].pop(key, None)

    async def broadcast_presence(self, topic_id: int) -> None:
        await self.broadcast(topic_id, self.presence(topic_id))


hub = Hub()
