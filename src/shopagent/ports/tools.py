from __future__ import annotations

from typing import Any, Protocol


class ToolClient(Protocol):
    """Transport-neutral tool boundary consumed by every domain Agent."""

    async def call(self, name: str, *, agent_name: str, **arguments: Any) -> dict[str, Any]: ...
