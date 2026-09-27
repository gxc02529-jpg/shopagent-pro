from __future__ import annotations

import logging

from shopagent.domain.models import AgentResult, ChatMessage
from shopagent.ports.tools import ToolClient

logger = logging.getLogger(__name__)


async def curated_feedback_answer(
    tools: ToolClient,
    *,
    agent_name: str,
    query: str,
    domain: str,
    message: ChatMessage | None = None,
) -> AgentResult | None:
    """Reuse reviewed feedback without overriding authoritative transactional data."""
    try:
        data = await tools.call(
            "knowledge.search",
            agent_name=agent_name,
            query=query,
            domain=domain,
            limit=3,
            tenant_id=str((message.context if message else {}).get("tenant_id", "global")),
            department=(message.context if message else {}).get("department"),
            channel=message.channel.value if message else None,
        )
    except Exception:  # noqa: BLE001 - optional enrichment must not block core commerce flows
        logger.warning("optional curated-knowledge lookup failed for %s", agent_name)
        return None
    hit = next(
        (
            item
            for item in data.get("hits", [])
            if str(item.get("document_id", "")).startswith("KB-FEEDBACK-")
        ),
        None,
    )
    if not hit:
        return None
    return AgentResult(
        answer=(
            f"{hit['excerpt']}\n依据：{hit['title']}（{hit['source']}，"
            f"版本 {hit['version']}，知识编号 {hit['document_id']}）"
        ),
        data=data,
        tools_used=["knowledge.search"],
    )
