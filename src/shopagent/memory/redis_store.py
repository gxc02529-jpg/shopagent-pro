from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

from shopagent.domain.models import SessionMemory


class RedisMemoryStore:
    """Redis-backed session memory with user/session isolation and sliding TTL."""

    def __init__(self, url: str, *, ttl_seconds: int = 1800, key_prefix: str = "shopagent") -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError('Redis backend requires: pip install -e ".[enterprise]"') from exc
        self._redis = Redis.from_url(url, decode_responses=True, health_check_interval=30)
        self._ttl = ttl_seconds
        self._prefix = key_prefix

    def _key(self, user_id: str, session_id: str) -> str:
        scoped = f"{len(user_id)}:{user_id}{len(session_id)}:{session_id}"
        digest = hashlib.sha256(scoped.encode("utf-8")).hexdigest()
        return f"{self._prefix}:memory:{digest}"

    @asynccontextmanager
    async def lock(self, user_id: str, session_id: str):
        # A distributed per-session lease prevents concurrent requests from
        # performing a lossy GET/modify/SET sequence on different pods.
        lock = self._redis.lock(
            f"{self._key(user_id, session_id)}:lock",
            timeout=60,
            blocking_timeout=10,
        )
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError("session is busy; retry the request")
        try:
            yield
        finally:
            await lock.release()

    async def get(self, user_id: str, session_id: str) -> SessionMemory:
        key = self._key(user_id, session_id)
        payload = await self._redis.get(key)
        if not payload:
            return SessionMemory(user_id=user_id, session_id=session_id)
        await self._redis.expire(key, self._ttl)
        memory = SessionMemory.model_validate_json(payload)
        if memory.user_id != user_id or memory.session_id != session_id:
            raise PermissionError("session memory scope mismatch")
        return memory

    async def save(self, memory: SessionMemory) -> None:
        await self._redis.set(
            self._key(memory.user_id, memory.session_id),
            memory.model_dump_json(),
            ex=self._ttl,
        )

    async def clear(self, user_id: str, session_id: str) -> None:
        await self._redis.delete(self._key(user_id, session_id))

    async def health(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()
