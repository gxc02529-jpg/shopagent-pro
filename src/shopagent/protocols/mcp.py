from __future__ import annotations

import json
import logging
from typing import Any

from shopagent.tools.registry import ToolInputError, ToolRegistry

logger = logging.getLogger(__name__)


class MCPServerAdapter:
    """MCP 2025-11-25 JSON-RPC subset over stateless Streamable HTTP POST."""

    protocol_version = "2025-11-25"

    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    async def handle(self, payload: dict[str, Any], *, agent_name: str) -> dict[str, Any] | None:
        request_id = payload.get("id")
        method = payload.get("method")
        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "shopagent-mcp",
                        "title": "ShopAgent MCP Tools",
                        "version": "1.0.0",
                    },
                    "instructions": (
                        "Discover tools with tools/list. Production calls are scoped by the "
                        "signed service token's Agent identity."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self._tools.mcp_tools(agent_name)}
            elif method == "tools/call":
                params = payload.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return self._error(request_id, -32602, "Invalid tools/call params")
                try:
                    structured = await self._tools.call(name, agent_name=agent_name, **arguments)
                    result = {
                        "content": [
                            {"type": "text", "text": json.dumps(structured, ensure_ascii=False)}
                        ],
                        "structuredContent": structured,
                        "isError": False,
                    }
                except ToolInputError as exc:
                    return self._error(request_id, -32602, str(exc))
                except (KeyError, PermissionError) as exc:
                    return self._error(request_id, -32602, str(exc))
                except TimeoutError:
                    result = {
                        "content": [{"type": "text", "text": "tool temporarily unavailable"}],
                        "isError": True,
                    }
                except (RuntimeError, ValueError):
                    result = {
                        "content": [{"type": "text", "text": "tool request failed"}],
                        "isError": True,
                    }
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception:
            logger.exception("MCP request failed: method=%s", method)
            return self._error(request_id, -32603, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(
        request_id: Any, code: int, message: str, data: dict | None = None
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
