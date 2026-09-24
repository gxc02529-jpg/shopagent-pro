import asyncio

import pytest

from shopagent.adapters.mock_pim import MockPIMAdapter
from shopagent.tools.product_tools import register_product_tools
from shopagent.tools.registry import ToolRegistry, ToolSpec


def test_tool_authorization_and_stock():
    registry = ToolRegistry()
    register_product_tools(registry, MockPIMAdapter())
    result = asyncio.run(
        registry.call("product.stock", agent_name="product_agent", product_id="SKU-1001")
    )
    assert result == {"product_id": "SKU-1001", "stock": 126, "available": True}
    with pytest.raises(PermissionError):
        asyncio.run(registry.call("product.stock", agent_name="order_agent", product_id="SKU-1001"))


def test_tool_timeout_opens_circuit_and_is_observable():
    async def slow_tool() -> dict:
        await asyncio.sleep(0.03)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "test.slow",
            "slow test tool",
            "1.0.0",
            slow_tool,
            frozenset({"test_agent"}),
            timeout_seconds=0.001,
            failure_threshold=1,
            cooldown_seconds=60,
        )
    )
    with pytest.raises(TimeoutError):
        asyncio.run(registry.call("test.slow", agent_name="test_agent"))
    with pytest.raises(RuntimeError, match="circuit open"):
        asyncio.run(registry.call("test.slow", agent_name="test_agent"))
    metrics = registry.metrics()["test.slow"]
    assert metrics["calls"] == 1
    assert metrics["failures"] == 1
    assert metrics["circuit_open"] is True
