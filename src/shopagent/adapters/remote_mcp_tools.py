from __future__ import annotations

from itertools import count
from typing import Any

from shopagent.security.tokens import issue_token


class RemoteMCPToolClient:
    """MCP Streamable-HTTP client used when tools run behind a service boundary."""

    def __init__(
        self,
        endpoint: str,
        *,
        service_secret: str,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._endpoint = endpoint
        self._service_secret = service_secret
        self._timeout = timeout_seconds
        self._ids = count(1)

    async def call(self, name: str, *, agent_name: str, **arguments: Any) -> dict[str, Any]:
        import httpx

        token = issue_token(
            self._service_secret,
            subject=agent_name,
            role="service",
            agent=agent_name,
            ttl_seconds=60,
        )
        request_id = next(self._ids)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
            # Kept for development interoperability. Production derives the
            # effective identity from the signed token and ignores this value.
            "X-Agent-Name": agent_name,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, json=payload, headers=headers)
            response.raise_for_status()
        body = response.json()
        if error := body.get("error"):
            raise RuntimeError(f"MCP error {error.get('code')}: {error.get('message')}")
        result = body.get("result") or {}
        if result.get("isError"):
            raise RuntimeError("MCP tool invocation failed")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise TypeError("MCP response did not include structuredContent")
        return structured
