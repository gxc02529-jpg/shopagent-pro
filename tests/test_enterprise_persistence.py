import asyncio

from shopagent.adapters.mock_commerce import DEFAULT_ORDERS
from shopagent.adapters.mysql_commerce import MySQLCommerceAdapter
from shopagent.adapters.mysql_knowledge import MySQLKnowledgeAdapter
from shopagent.domain.models import (
    AuditEvent,
    Intent,
    InteractionRecord,
    KnowledgeCandidate,
    KnowledgeDocument,
)
from shopagent.operations.mysql_store import MySQLOperationsStore


def test_sql_operations_survive_store_recreation(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'enterprise.db'}"
    first = MySQLOperationsStore(database_url)
    interaction = InteractionRecord(
        trace_id="trace-persisted",
        user_id="enterprise-user",
        session_id="session-1",
        question="退款规则是什么",
        answer="规则回答",
        intent=Intent.AFTER_SALES,
        routed_agent="after_sales_agent",
        tools_used=["knowledge.search"],
    )
    candidate = KnowledgeCandidate(
        id="KC-PERSISTED",
        trace_id=interaction.trace_id,
        question=interaction.question,
        current_answer=interaction.answer,
        suggested_answer="改进答案",
        reason="回归测试",
        domain="after_sales",
    )
    asyncio.run(first.record_interaction(interaction))
    asyncio.run(first.save_candidate(candidate))
    asyncio.run(
        first.record_audit(
            AuditEvent(
                id="AUD-PERSISTED",
                event_type="test.event",
                actor_id="tester",
                entity_id=candidate.id,
                trace_id=interaction.trace_id,
            )
        )
    )
    asyncio.run(first.close())

    recreated = MySQLOperationsStore(database_url)
    loaded = asyncio.run(recreated.get_interaction("trace-persisted"))
    candidates = asyncio.run(recreated.list_candidates("pending"))
    audits = asyncio.run(recreated.list_audit())
    assert loaded is not None
    assert loaded.user_id == "enterprise-user"
    assert candidates[0].id == "KC-PERSISTED"
    assert audits[0].id == "AUD-PERSISTED"
    assert asyncio.run(recreated.health()) is True
    asyncio.run(recreated.close())


def test_sql_knowledge_publish_survives_adapter_recreation(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'knowledge.db'}"
    first = MySQLKnowledgeAdapter(database_url)
    document = KnowledgeDocument(
        id="KB-PERSISTED",
        domain="after_sales",
        title="企业持久化知识",
        content="这条知识在服务重启后仍然存在。",
        keywords=["持久化"],
        version="v1",
        source="运营审核",
    )
    asyncio.run(first.upsert(document))
    asyncio.run(first.close())

    recreated = MySQLKnowledgeAdapter(database_url)
    documents = asyncio.run(recreated.list_documents("after_sales"))
    assert [item.id for item in documents] == ["KB-PERSISTED"]
    assert asyncio.run(recreated.health()) is True
    asyncio.run(recreated.close())


def test_sql_ticket_is_idempotent_and_survives_adapter_recreation(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'commerce.db'}"
    first = MySQLCommerceAdapter(database_url, seed_orders=DEFAULT_ORDERS)
    created = asyncio.run(
        first.create(
            user_id="demo-user",
            order_id="ORD-20260002",
            issue_type="换货",
            description="商品破损",
            idempotency_key="persistent-ticket-request",
        )
    )
    repeated = asyncio.run(
        first.create(
            user_id="demo-user",
            order_id="ORD-20260002",
            issue_type="换货",
            description="商品破损",
            idempotency_key="persistent-ticket-request",
        )
    )
    assert repeated.id == created.id
    asyncio.run(first.close())

    recreated = MySQLCommerceAdapter(database_url)
    loaded = asyncio.run(recreated.get_ticket_for_user("demo-user", created.id))
    assert loaded is not None
    assert loaded.id == created.id
    assert asyncio.run(recreated.health()) is True
    asyncio.run(recreated.close())
