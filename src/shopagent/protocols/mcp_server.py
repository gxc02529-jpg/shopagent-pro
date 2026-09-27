from __future__ import annotations

import functools
import logging
import secrets
from contextvars import ContextVar
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import McpError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware.middleware import Middleware
from fastmcp.tools import Tool
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from shopagent.security.tokens import Principal, TokenError, verify_token
from shopagent.settings import Settings
from shopagent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# The authenticated agent identity for the in-flight request, resolved by the
# service-auth middleware and consumed by every tool wrapper at call time.
_agent_ctx: ContextVar[str] = ContextVar("shopagent_mcp_agent", default="")


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    return None


class ServiceAuthHTTPMiddleware(BaseHTTPMiddleware):
    """Resolve the caller's agent identity at the HTTP boundary.

    Mirrors :func:`shopagent.api.require_service` so the security boundary is
    identical: production rejects any request without a valid, non-anonymous
    service token with a real ``401`` (or ``403`` for a token lacking an agent),
    while development falls back to the ``X-Agent-Name`` header. The resolved
    agent is stashed on ``request.state`` for the governance middleware below.
    """

    def __init__(self, app: Any, *, settings: Settings) -> None:
        super().__init__(app)
        self._service_token = settings.service_token
        self._production = settings.env.lower() in {"production", "prod"}

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        agent, error = self._resolve_agent(request)
        if error is not None:
            return JSONResponse({"detail": error}, status_code=error[1])
        request.state.shopagent_agent = agent
        return await call_next(request)

    def _resolve_agent(self, request: Any) -> tuple[str, tuple[str, int] | None]:
        token = _bearer_token(request.headers.get("authorization")) or request.headers.get(
            "x-service-token"
        )
        x_agent_name = request.headers.get("x-agent-name") or "product_agent"
        if token:
            try:
                principal: Principal = verify_token(
                    self._service_token, token, expected_role="service"
                )
                if principal.agent:
                    return principal.agent, None
                if self._production:
                    return "", ("service token has no agent identity", 403)
            except TokenError:
                if self._production:
                    return "", ("invalid service token", 401)
                if secrets.compare_digest(token, self._service_token):
                    return x_agent_name, None
        if self._production:
            return "", ("service authentication required", 401)
        return x_agent_name, None


class ToolGovernanceMiddleware(Middleware):
    """Enforce per-agent tool scope at the MCP boundary.

    ``tools/list`` intentionally stays unfiltered (call-layer authorization), while
    ``tools/call`` rejects out-of-scope agents with the same ``-32602`` code the
    previous hand-rolled adapter used. The actual invocation still flows through
    :meth:`ToolRegistry.call`, so circuit breaker, timeout and metrics are unchanged.
    """

    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        name = context.message.name
        agent = self._current_agent()
        try:
            self._tools.authorize(name, agent)
        except (KeyError, PermissionError) as exc:
            raise McpError(-32602, str(exc)) from exc
        return await call_next(context)

    def _current_agent(self) -> str:
        try:
            request = get_http_request()
            agent = getattr(request.state, "shopagent_agent", "")
        except RuntimeError:
            agent = ""
        return agent or _agent_ctx.get()


class FastMCPServerAdapter:
    """FastMCP-backed replacement for the hand-rolled MCP JSON-RPC adapter.

    Wraps the existing :class:`ToolRegistry` so the governance core (agent
    authorization, circuit breaker, timeout, metrics) is untouched; only the wire
    protocol layer is swapped for the maintained FastMCP 4.x implementation.

    Tool-execution failures (circuit open, timeout) raise from the wrapper and are
    surfaced by FastMCP as ``isError: true`` results, keeping ``-32602`` reserved for
    authorization and argument-contract violations — the same split as before.
    """

    def __init__(self, tools: ToolRegistry, settings: Settings) -> None:
        self._tools = tools
        self._settings = settings
        self._mcp = FastMCP(
            name="shopagent-mcp",
            instructions=(
                "Discover tools with tools/list. Production calls are scoped by the "
                "signed service token's Agent identity."
            ),
            middleware=[ToolGovernanceMiddleware(tools)],
        )
        for spec in tools.specs():
            self._mcp.add_tool(self._build_tool(spec))

    def _build_tool(self, spec: Any) -> Tool:
        async def handler(**arguments: Any) -> dict[str, Any]:
            agent = self._current_agent()
            return await self._tools.call(spec.name, agent_name=agent, **arguments)

        functools.wraps(spec.handler)(handler)
        return Tool.from_function(handler, name=spec.name, description=spec.description)

    def _current_agent(self) -> str:
        try:
            request = get_http_request()
            agent = getattr(request.state, "shopagent_agent", "")
        except RuntimeError:
            agent = ""
        return agent or _agent_ctx.get()

    def http_app(self) -> Any:
        # stateless_http keeps the wire contract identical to the previous adapter
        # (one POST per JSON-RPC message, no session handshake); json_response keeps
        # responses as plain application/json instead of SSE. The HTTP auth middleware
        # runs before the MCP router so production rejects missing/invalid tokens with
        # a real 401/403 status rather than a JSON-RPC error body.
        return self._mcp.http_app(
            path="/",
            stateless_http=True,
            json_response=True,
            middleware=[StarletteMiddleware(ServiceAuthHTTPMiddleware, settings=self._settings)],
        )
