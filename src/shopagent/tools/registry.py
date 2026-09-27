from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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
    """Tool discovery + authorization + invocation boundary.

    Wire-protocol concerns (MCP JSON-RPC, tool schemas) live in the protocol
    adapters; this registry owns the durable governance core: which agent may
    call which tool, the circuit breaker, timeout and call metrics.
    """

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

    def authorize(self, name: str, agent_name: str) -> ToolSpec:
        """Raise the discovery/authorization errors for a tool call without executing it.

        Kept separate from :meth:`call` so protocol adapters can enforce the same
        agent scope at the boundary (e.g. an MCP ``tools/call`` handler) and map the
        error to the protocol's own error code before any side effect runs.
        """
        spec = self._tools.get(name)
        if not spec:
            raise KeyError(f"unknown tool: {name}")
        if agent_name not in spec.allowed_agents:
            raise PermissionError(f"agent {agent_name} cannot call {name}")
        return spec

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    async def call(self, name: str, *, agent_name: str, **arguments: Any) -> dict[str, Any]:
        spec = self.authorize(name, agent_name)
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
