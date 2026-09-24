from __future__ import annotations

import re

from shopagent.agents.knowledge_support import curated_feedback_answer
from shopagent.domain.models import AgentResult, ChatMessage, IntentResult, SessionMemory
from shopagent.ports.tools import ToolClient


class RecommendationAgent:
    name = "recommendation_agent"
    _budget_pattern = re.compile(r"(?:预算|价格|不超过|以内|以下)\D{0,4}(\d+(?:\.\d+)?)")

    def __init__(self, tools: ToolClient) -> None:
        self._tools = tools

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        if curated := await curated_feedback_answer(
            self._tools,
            agent_name=self.name,
            query=message.content,
            domain="product",
        ):
            return curated
        data = await self._tools.call(
            "product.search", agent_name=self.name, query=message.content, limit=10
        )
        products = data["products"]
        budget_match = self._budget_pattern.search(message.content)
        budget = float(budget_match.group(1)) if budget_match else None
        if budget is not None:
            products = [item for item in products if item["price"] <= budget]
        products = sorted(products, key=lambda item: (item["stock"] <= 0, item["price"]))[:3]
        data.update({"products": products, "budget": budget})
        if not products:
            qualifier = f"预算 ¥{budget:.0f} 以内" if budget is not None else "当前条件下"
            return AgentResult(
                answer=f"{qualifier}暂时没有合适商品。可以提高预算或告诉我更具体的品类偏好。",
                data=data,
                tools_used=["product.search"],
            )
        lines = [
            f"- {item['name']}（{item['id']}）：¥{item['price']:.2f}，{item['description']}"
            for item in products
        ]
        budget_text = f"，并按 ¥{budget:.0f} 的预算筛选" if budget is not None else ""
        return AgentResult(
            answer="结合你的使用场景" + budget_text + "，推荐：\n" + "\n".join(lines),
            data=data,
            tools_used=["product.search"],
        )
