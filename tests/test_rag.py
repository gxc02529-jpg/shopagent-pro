import asyncio

from shopagent.adapters.mock_knowledge import MockKnowledgeAdapter
from shopagent.container import build_container
from shopagent.domain.models import ChatMessage, Intent
from shopagent.rag.service import KnowledgeService
from shopagent.settings import Settings


def test_retrieval_ranks_relevant_policy_and_keeps_citation():
    service = KnowledgeService(MockKnowledgeAdapter())
    hits = asyncio.run(service.search("退货的运费险怎么理赔", domain="after_sales"))
    assert hits[0].document_id == "KB-AFTER-003"
    assert hits[0].source == "保险服务说明"
    assert hits[0].score > 0.5


def test_policy_question_uses_knowledge_without_creating_ticket():
    container = build_container(Settings())
    result = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="rag-session",
                content="七天无理由退货有什么条件？",
            )
        )
    )
    assert result.intent.intent == Intent.AFTER_SALES
    assert result.tools_used == ["knowledge.search"]
    assert "KB-AFTER-001" in result.answer
    assert "版本 2026.02" in result.answer
    assert result.need_human is False


def test_action_request_still_creates_ticket_instead_of_rag_answer():
    container = build_container(Settings())
    result = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="rag-action-session",
                content="请帮我为订单 ORD-20260002 申请退货",
            )
        )
    )
    assert result.tools_used == ["after_sales.create"]
    assert result.data["ticket"] is not None
