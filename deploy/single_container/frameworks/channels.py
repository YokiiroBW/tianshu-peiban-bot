"""Bounded application bytes, without sockets or HTTP framing."""

import asyncio
from collections import deque


class ByteChannel:
    def __init__(self, limit):
        self.limit = limit
        self.buffered = self.peak = 0
        self._parts = deque()
        self._condition = asyncio.Condition()
        self._closed = False
        self._error = None

    async def send(self, data):
        view = memoryview(data)
        for offset in range(0, len(view), self.limit):
            part = bytes(view[offset : offset + self.limit])
            size = len(part)
            async with self._condition:
                await self._condition.wait_for(
                    lambda size=size: self._closed or self.buffered + size <= self.limit
                )
                if self._closed:
                    raise self._error or ConnectionResetError("Local receiver closed")
                self._parts.append(part)
                self.buffered += len(part)
                self.peak = max(self.peak, self.buffered)
                self._condition.notify_all()

    async def receive(self):
        async with self._condition:
            await self._condition.wait_for(lambda: self._parts or self._closed)
            if self._parts:
                part = self._parts.popleft()
                self.buffered -= len(part)
                self._condition.notify_all()
                return part
            if self._error:
                raise self._error
            return None

    async def finish(self, error=None, *, discard=False):
        async with self._condition:
            if self._closed:
                if discard:
                    self._parts.clear()
                    self.buffered = 0
                    self._condition.notify_all()
                return
            self._closed = True
            if error is not None:
                self._error = error
            if discard:
                self._parts.clear()
                self.buffered = 0
            self._condition.notify_all()

    async def drain(self):
        async with self._condition:
            await self._condition.wait_for(lambda: not self._parts or self._error)
            if self._error:
                raise self._error
