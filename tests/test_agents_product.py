import asyncio

from shopagent.agents.product import ProductAgent
from shopagent.domain.models import ChatMessage, Intent, IntentResult, SessionMemory


class _FakeRegistry:
    """Returns canned tool results keyed by tool name for ProductAgent tests."""

    def __init__(self, payloads: dict) -> None:
        self._payloads = payloads

    async def call(self, name: str, *, agent_name: str, **arguments):
        return self._payloads[name]


def _message(content: str) -> ChatMessage:
    return ChatMessage(user_id="u", session_id="s", content=content)


def _memory(product_id: str = "") -> SessionMemory:
    memory = SessionMemory(user_id="u", session_id="s")
    if product_id:
        memory.context["product_id"] = product_id
    return memory


def _intent(intent: Intent) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9)


def test_stock_query_in_stock():
    registry = _FakeRegistry(
        {"product.stock": {"product_id": "SKU-1001", "stock": 5, "available": True}}
    )
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("有货吗"), _intent(Intent.STOCK_QUERY), _memory("SKU-1001"))
    )
    assert "有货" in result.answer and "5" in result.answer
    assert result.tools_used == ["product.stock"]


def test_stock_query_out_of_stock():
    registry = _FakeRegistry(
        {"product.stock": {"product_id": "SKU-1002", "stock": 0, "available": False}}
    )
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("有货吗"), _intent(Intent.STOCK_QUERY), _memory("SKU-1002"))
    )
    assert "缺货" in result.answer


def test_stock_query_unknown_product():
    registry = _FakeRegistry(
        {"product.stock": {"product_id": "SKU-X", "stock": None, "available": False}}
    )
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("有货吗"), _intent(Intent.STOCK_QUERY), _memory("SKU-X"))
    )
    assert "没有找到商品" in result.answer


def test_product_detail_found():
    product = {
        "id": "SKU-1001",
        "name": "AirBeat",
        "price": 899.0,
        "attributes": {"颜色": "黑"},
        "description": "降噪耳机",
    }
    registry = _FakeRegistry({"product.detail": {"product": product}})
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("详情"), _intent(Intent.PRODUCT_DETAIL), _memory("SKU-1001"))
    )
    assert "AirBeat" in result.answer
    assert "¥899.00" in result.answer
    assert "颜色：黑" in result.answer


def test_product_detail_not_found():
    registry = _FakeRegistry({"product.detail": {"product": None}})
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("详情"), _intent(Intent.PRODUCT_DETAIL), _memory("SKU-1001"))
    )
    assert "没有找到商品" in result.answer


def test_product_search_recommendation_prefix_and_stock_status():
    products = [
        {"id": "SKU-1001", "name": "AirBeat", "price": 899.0, "stock": 5},
        {"id": "SKU-2001", "name": "Bag", "price": 269.0, "stock": 0},
    ]
    registry = _FakeRegistry({"product.search": {"products": products, "count": 2}})
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("降噪耳机"), _intent(Intent.RECOMMENDATION), _memory())
    )
    assert "推荐" in result.answer
    assert "AirBeat" in result.answer
    assert "暂时缺货" in result.answer  # SKU-2001 stock == 0


def test_product_search_default_prefix():
    products = [{"id": "SKU-1001", "name": "AirBeat", "price": 899.0, "stock": 5}]
    registry = _FakeRegistry({"product.search": {"products": products, "count": 1}})
    agent = ProductAgent(registry)
    result = asyncio.run(agent.execute(_message("耳机"), _intent(Intent.PRODUCT_SEARCH), _memory()))
    assert "为你找到" in result.answer


def test_product_search_empty():
    registry = _FakeRegistry({"product.search": {"products": [], "count": 0}})
    agent = ProductAgent(registry)
    result = asyncio.run(
        agent.execute(_message("未知品类"), _intent(Intent.PRODUCT_SEARCH), _memory())
    )
    assert "暂时没有检索到" in result.answer
