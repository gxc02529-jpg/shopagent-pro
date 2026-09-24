from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from copy import deepcopy

from shopagent.domain.models import SessionMemory


class InMemoryTTLStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._items: dict[tuple[str, str], tuple[float, SessionMemory]] = {}
        self._lock = asyncio.Lock()
        self._session_locks: dict[tuple[str, str], asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(self, user_id: str, session_id: str):
        key = self._key(user_id, session_id)
        async with self._lock:
            session_lock = self._session_locks.setdefault(key, asyncio.Lock())
        async with session_lock:
            yield

    @staticmethod
    def _key(user_id: str, session_id: str) -> tuple[str, str]:
        return user_id, session_id

    async def get(self, user_id: str, session_id: str) -> SessionMemory:
        key = self._key(user_id, session_id)
        async with self._lock:
            item = self._items.get(key)
            if item and item[0] > time.monotonic():
                return deepcopy(item[1])
            self._items.pop(key, None)
        return SessionMemory(user_id=user_id, session_id=session_id)

    async def save(self, memory: SessionMemory) -> None:
        key = self._key(memory.user_id, memory.session_id)
        async with self._lock:
            self._items[key] = (time.monotonic() + self._ttl, deepcopy(memory))

    async def clear(self, user_id: str, session_id: str) -> None:
        async with self._lock:
            self._items.pop(self._key(user_id, session_id), None)

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None
