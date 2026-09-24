from __future__ import annotations

from shopagent.rag.service import KnowledgeService
from shopagent.tools.registry import ToolRegistry, ToolSpec


def register_knowledge_tools(registry: ToolRegistry, knowledge: KnowledgeService) -> None:
    async def search_knowledge(query: str, domain: str = "after_sales", limit: int = 3) -> dict:
        hits = await knowledge.search(query, domain=domain, limit=min(max(limit, 1), 5))
        return {"hits": [item.model_dump() for item in hits], "count": len(hits)}

    registry.register(
        ToolSpec(
            "knowledge.search",
            "检索带版本和来源的业务知识",
            "1.0.0",
            search_knowledge,
            frozenset(
                {
                    "product_agent",
                    "recommendation_agent",
                    "order_agent",
                    "after_sales_agent",
                }
            ),
        )
    )
