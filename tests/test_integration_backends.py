import asyncio
import os
from uuid import uuid4

import pytest

from shopagent.adapters.mock_commerce import DEFAULT_ORDERS
from shopagent.adapters.mysql_commerce import MySQLCommerceAdapter
from shopagent.domain.models import Intent, InteractionRecord, SessionMemory
from shopagent.memory.redis_store import RedisMemoryStore
from shopagent.operations.mysql_store import MySQLOperationsStore


@pytest.mark.integration
def test_live_redis_round_trip():
    url = os.getenv("SHOPAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("SHOPAGENT_TEST_REDIS_URL is not configured")

    async def scenario() -> None:
        # redis.asyncio pools are bound to the event loop that opens their
        # connection. Keep the whole client lifecycle on one loop, matching
        # the ASGI application's production lifecycle.
        store = RedisMemoryStore(url, ttl_seconds=60, key_prefix=f"shopagent-test-{uuid4().hex}")
        memory = SessionMemory(user_id="integration-user", session_id="integration-session")
        memory.context["verified"] = True
        try:
            await store.save(memory)
            loaded = await store.get(memory.user_id, memory.session_id)
            assert loaded.context == {"verified": True}
            assert await store.health() is True
            await store.clear(memory.user_id, memory.session_id)
        finally:
            await store.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_live_mysql_round_trip():
    url = os.getenv("SHOPAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SHOPAGENT_TEST_DATABASE_URL is not configured")
    store = MySQLOperationsStore(url)
    trace_id = f"integration-{uuid4().hex}"
    record = InteractionRecord(
        trace_id=trace_id,
        user_id="integration-user",
        session_id="integration-session",
        question="integration question",
        answer="integration answer",
        intent=Intent.PRODUCT_SEARCH,
    )
    asyncio.run(store.record_interaction(record))
    loaded = asyncio.run(store.get_interaction(trace_id))
    assert loaded is not None
    assert loaded.answer == "integration answer"
    assert asyncio.run(store.health()) is True
    asyncio.run(store.close())

    commerce = MySQLCommerceAdapter(url, seed_orders=DEFAULT_ORDERS)
    request_key = f"integration-ticket-{uuid4().hex}"
    created = asyncio.run(
        commerce.create(
            user_id="demo-user",
            order_id="ORD-20260002",
            issue_type="换货",
            description="CI integration ticket",
            idempotency_key=request_key,
        )
    )
    loaded = asyncio.run(commerce.get_ticket_for_user("demo-user", created.id))
    assert loaded is not None
    assert loaded.id == created.id
    asyncio.run(commerce.close())
