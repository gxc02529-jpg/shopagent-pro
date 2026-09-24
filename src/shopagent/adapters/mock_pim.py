from __future__ import annotations

from shopagent.domain.models import Product

DEFAULT_PRODUCTS = [
    Product(
        id="SKU-1001",
        name="AirBeat Pro 降噪耳机",
        category="数码/耳机",
        price=899.0,
        stock=126,
        attributes={"颜色": "曜石黑/云雾白", "续航": "38小时", "降噪": "45dB"},
        description="双设备连接，适合通勤、差旅和开放式办公室。",
    ),
    Product(
        id="SKU-1002",
        name="AirBeat Lite 真无线耳机",
        category="数码/耳机",
        price=329.0,
        stock=0,
        attributes={"颜色": "白色/浅蓝", "续航": "24小时", "防水": "IPX5"},
        description="轻量半入耳设计，适合运动和日常使用。",
    ),
    Product(
        id="SKU-2001",
        name="UrbanGo 通勤双肩包",
        category="箱包/双肩包",
        price=269.0,
        stock=58,
        attributes={"容量": "22L", "电脑仓": "16英寸", "材质": "防泼水尼龙"},
        description="独立电脑仓与行李箱固定带，适合日常通勤。",
    ),
    Product(
        id="SKU-3001",
        name="PureWarm 恒温杯",
        category="家居/杯具",
        price=159.0,
        stock=203,
        attributes={"容量": "420ml", "保温": "6小时", "材质": "316不锈钢"},
        description="杯盖温度显示，适合办公与出行。",
    ),
]


class MockPIMAdapter:
    """A deterministic PIM adapter that can be replaced without changing tools."""

    def __init__(self, products: list[Product] | None = None) -> None:
        self._products = {item.id: item for item in (products or DEFAULT_PRODUCTS)}

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        normalized = query.lower().strip()
        tokens = [token for token in normalized.replace("，", " ").split() if token]

        def score(product: Product) -> int:
            haystack = " ".join(
                [
                    product.id,
                    product.name,
                    product.category,
                    product.description,
                    *product.attributes.values(),
                ]
            ).lower()
            direct = 5 if normalized and normalized in haystack else 0
            return direct + sum(1 for token in tokens if token in haystack)

        ranked = sorted(self._products.values(), key=score, reverse=True)
        matched = [item for item in ranked if score(item) > 0]
        if not matched:
            keywords = {
                "耳机": "耳机",
                "降噪": "降噪",
                "通勤": "通勤",
                "背包": "双肩包",
                "杯": "杯",
            }
            selected = next((value for key, value in keywords.items() if key in normalized), "")
            matched = [
                item for item in ranked if selected and selected in (item.name + item.description)
            ]
        return matched[:limit]

    async def get(self, product_id: str) -> Product | None:
        return self._products.get(product_id.upper())

    async def stock(self, product_id: str) -> int | None:
        product = await self.get(product_id)
        return product.stock if product else None
