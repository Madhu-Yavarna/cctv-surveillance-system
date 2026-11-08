"""Station-scoped WebSocket hub. Admin connections get response updates."""
import asyncio
from collections import defaultdict
from fastapi import WebSocket


class Hub:
    def __init__(self):
        self.police: dict[int, set[WebSocket]] = defaultdict(set)
        self.admins: set[WebSocket] = set()
        self.loop = None

    async def connect(self, ws: WebSocket, user):
        await ws.accept()
        (self.admins if user.role == "admin" else self.police[user.station_id]).add(ws)

    def disconnect(self, ws: WebSocket, user):
        (self.admins if user.role == "admin" else self.police[user.station_id]).discard(ws)

    async def _send(self, ws, msg):
        try:
            await ws.send_json(msg); return True
        except Exception:
            return False

    async def to_station(self, station_id: int, msg: dict) -> bool:
        """True if at least one officer connection received it."""
        res = await asyncio.gather(*[self._send(w, msg) for w in list(self.police.get(station_id, ()))])
        return any(res)

    async def to_admins(self, msg: dict):
        await asyncio.gather(*[self._send(w, msg) for w in list(self.admins)])

    async def to_all(self, msg: dict):
        tasks = [self._send(w, msg) for w in list(self.admins)]
        for st_set in self.police.values():
            tasks.extend(self._send(w, msg) for w in list(st_set))
        if tasks:
            await asyncio.gather(*tasks)

    def sync_to_station(self, station_id: int, msg: dict):
        """Thread-safe synchronous wrapper for background worker threads."""
        if self.loop and not self.loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.to_station(station_id, msg), self.loop)
            except Exception as e:
                print("WebSocket sync_to_station error:", e)

    def sync_to_admins(self, msg: dict):
        """Thread-safe synchronous wrapper for background worker threads."""
        if self.loop and not self.loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.to_admins(msg), self.loop)
            except Exception as e:
                print("WebSocket sync_to_admins error:", e)


hub = Hub()
