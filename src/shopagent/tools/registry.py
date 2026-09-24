from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


class ToolInputError(ValueError):
    """Caller-supplied arguments do not match the registered tool contract."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    version: str
    handler: ToolHandler
    allowed_agents: frozenset[str]
    timeout_seconds: float = 2.0
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0


@dataclass(slots=True)
class ToolRuntime:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    total_latency_ms: float = 0.0
    circuit_open_until: float = 0.0


class ToolRegistry:
    """Small MCP-compatible boundary: discovery + authorization + invocation."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._runtime: dict[str, ToolRuntime] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        self._runtime[spec.name] = ToolRuntime()

    def discover(self, agent_name: str) -> list[dict[str, str]]:
        return [
            {"name": spec.name, "description": spec.description, "version": spec.version}
            for spec in self._tools.values()
            if agent_name in spec.allowed_agents
        ]

    def mcp_tools(self, agent_name: str) -> list[dict[str, Any]]:
        return [
            self._mcp_definition(spec)
            for spec in self._tools.values()
            if agent_name in spec.allowed_agents
        ]

    @staticmethod
    def _mcp_definition(spec: ToolSpec) -> dict[str, Any]:
        properties: dict[str, dict[str, str]] = {}
        required: list[str] = []
        type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
        type_hints = get_type_hints(spec.handler)
        for name, parameter in inspect.signature(spec.handler).parameters.items():
            annotation = type_hints.get(name, parameter.annotation)
            properties[name] = {"type": type_map.get(annotation, "string")}
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        read_only = not spec.name.startswith("after_sales.create")
        return {
            "name": spec.name,
            "title": spec.name.replace(".", " ").title(),
            "description": spec.description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": False,
                "idempotentHint": read_only,
                "openWorldHint": True,
            },
        }

    async def call(self, name: str, *, agent_name: str, **arguments: Any) -> dict[str, Any]:
        spec = self._tools.get(name)
        if not spec:
            raise KeyError(f"unknown tool: {name}")
        if agent_name not in spec.allowed_agents:
            raise PermissionError(f"agent {agent_name} cannot call {name}")
        try:
            inspect.signature(spec.handler).bind(**arguments)
        except TypeError as exc:
            # Invalid caller input is not a dependency failure and must never trip
            # the shared circuit breaker for otherwise healthy traffic.
            raise ToolInputError("arguments do not match the tool input schema") from exc
        runtime = self._runtime[name]
        now = time.monotonic()
        if runtime.circuit_open_until > now:
            raise RuntimeError(f"tool circuit open: {name}")
        started = time.perf_counter()
        runtime.calls += 1
        try:
            result = await asyncio.wait_for(spec.handler(**arguments), timeout=spec.timeout_seconds)
        except Exception:
            runtime.failures += 1
            runtime.consecutive_failures += 1
            if runtime.consecutive_failures >= spec.failure_threshold:
                runtime.circuit_open_until = time.monotonic() + spec.cooldown_seconds
            raise
        else:
            runtime.successes += 1
            runtime.consecutive_failures = 0
            runtime.circuit_open_until = 0.0
            return result
        finally:
            runtime.total_latency_ms += (time.perf_counter() - started) * 1000

    def metrics(self) -> dict[str, dict[str, float | int | bool]]:
        now = time.monotonic()
        return {
            name: {
                "calls": item.calls,
                "successes": item.successes,
                "failures": item.failures,
                "avg_latency_ms": round(item.total_latency_ms / item.calls, 3)
                if item.calls
                else 0.0,
                "circuit_open": item.circuit_open_until > now,
            }
            for name, item in self._runtime.items()
        }
