from contextlib import AbstractAsyncContextManager
from typing import Protocol

from shopagent.domain.models import SessionMemory


class MemoryStore(Protocol):
    def lock(self, user_id: str, session_id: str) -> AbstractAsyncContextManager[None]: ...

    async def get(self, user_id: str, session_id: str) -> SessionMemory: ...

    async def save(self, memory: SessionMemory) -> None: ...

    async def clear(self, user_id: str, session_id: str) -> None: ...
