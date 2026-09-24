from __future__ import annotations

from shopagent.agents.knowledge_support import curated_feedback_answer
from shopagent.domain.models import AgentResult, ChatMessage, Intent, IntentResult, SessionMemory
from shopagent.ports.tools import ToolClient


class ProductAgent:
    name = "product_agent"

    def __init__(self, tools: ToolClient) -> None:
        self._tools = tools

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        product_id = intent.entities.get("product_id") or str(memory.context.get("product_id", ""))
        if intent.intent == Intent.STOCK_QUERY and product_id:
            result = await self._tools.call(
                "product.stock", agent_name=self.name, product_id=product_id
            )
            stock = result["stock"]
            if stock is None:
                answer = f"没有找到商品 {product_id}，请检查 SKU 是否正确。"
            elif stock > 0:
                answer = f"{product_id} 当前有货，库存 {stock} 件。库存实时变化，请以下单页为准。"
            else:
                answer = f"{product_id} 当前暂时缺货，可以稍后再看或选择相似商品。"
            return AgentResult(answer=answer, data=result, tools_used=["product.stock"])

        if intent.intent == Intent.PRODUCT_DETAIL and product_id:
            result = await self._tools.call(
                "product.detail", agent_name=self.name, product_id=product_id
            )
            product = result["product"]
            if not product:
                return AgentResult(
                    answer=f"没有找到商品 {product_id}，请检查 SKU 是否正确。",
                    data=result,
                    tools_used=["product.detail"],
                )
            attributes = "、".join(
                f"{key}：{value}" for key, value in product["attributes"].items()
            )
            answer = (
                f"{product['name']}（{product['id']}）售价 ¥{product['price']:.2f}。"
                f"{attributes}。{product['description']}"
            )
            return AgentResult(answer=answer, data=result, tools_used=["product.detail"])

        if curated := await curated_feedback_answer(
            self._tools,
            agent_name=self.name,
            query=message.content,
            domain="product",
        ):
            return curated

        result = await self._tools.call(
            "product.search", agent_name=self.name, query=message.content, limit=5
        )
        products = result["products"]
        if not products:
            return AgentResult(
                answer="暂时没有检索到匹配商品。你可以告诉我品类、预算和使用场景，我再帮你找。",
                data=result,
                tools_used=["product.search"],
            )
        lines = []
        for item in products[:3]:
            status = f"库存 {item['stock']} 件" if item["stock"] else "暂时缺货"
            lines.append(f"- {item['name']}（{item['id']}）：¥{item['price']:.2f}，{status}")
        prefix = "结合你的需求，推荐：" if intent.intent == Intent.RECOMMENDATION else "为你找到："
        return AgentResult(
            answer=prefix + "\n" + "\n".join(lines) + "\n回复 SKU 可继续查询参数或库存。",
            data=result,
            tools_used=["product.search"],
        )
