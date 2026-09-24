from __future__ import annotations

from shopagent.ports.products import ProductRepository
from shopagent.tools.registry import ToolRegistry, ToolSpec


def register_product_tools(registry: ToolRegistry, repository: ProductRepository) -> None:
    async def search_products(query: str, limit: int = 5) -> dict:
        products = await repository.search(query, limit=min(max(limit, 1), 10))
        return {"products": [item.model_dump() for item in products], "count": len(products)}

    async def get_product_detail(product_id: str) -> dict:
        product = await repository.get(product_id)
        return {"product": product.model_dump() if product else None}

    async def get_product_stock(product_id: str) -> dict:
        stock = await repository.stock(product_id)
        return {"product_id": product_id.upper(), "stock": stock, "available": bool(stock)}

    allowed = frozenset({"product_agent", "recommendation_agent"})
    registry.register(
        ToolSpec("product.search", "按自然语言检索商品", "1.0.0", search_products, allowed)
    )
    registry.register(
        ToolSpec("product.detail", "按 SKU 查询商品详情", "1.0.0", get_product_detail, allowed)
    )
    registry.register(
        ToolSpec("product.stock", "按 SKU 查询实时库存", "1.0.0", get_product_stock, allowed)
    )
