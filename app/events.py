from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def register(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def subscribe(self) -> AsyncIterator[dict]:
        queue = self.register()
        try:
            while True:
                yield await queue.get()
        finally:
            self.unregister(queue)
