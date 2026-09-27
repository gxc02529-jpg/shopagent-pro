from __future__ import annotations

from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from shopagent.security.tokens import issue_token


class RemoteMCPToolClient:
    """MCP Streamable-HTTP tool client used when tools run behind a service boundary.

    Replaces the hand-rolled httpx JSON-RPC client with the maintained
    ``fastmcp.Client`` + ``StreamableHttpTransport`` pair, while preserving the
    :class:`ToolClient` protocol the agents already depend on.
    """

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

    async def call(self, name: str, *, agent_name: str, **arguments: Any) -> dict[str, Any]:
        token = issue_token(
            self._service_secret,
            subject=agent_name,
            role="service",
            agent=agent_name,
            ttl_seconds=60,
        )
        transport = StreamableHttpTransport(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                # Kept for development interoperability. Production derives the
                # effective identity from the signed token and ignores this value.
                "X-Agent-Name": agent_name,
            },
        )
        async with Client(transport) as client:
            result = await client.call_tool(
                name, arguments=arguments, timeout=self._timeout, raise_on_error=False
            )
        if result.is_error:
            raise RuntimeError("MCP tool invocation failed")
        structured = result.structured_content
        if not isinstance(structured, dict):
            raise TypeError("MCP response did not include structuredContent")
        return structured

    async def close(self) -> None:
        # The client is created per call and closed by its own context manager.
        return None
