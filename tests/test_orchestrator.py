import asyncio

import pytest

from shopagent.container import build_container
from shopagent.domain.models import ChatMessage, Intent
from shopagent.settings import Settings


@pytest.fixture
def container():
    return build_container(Settings())


def test_product_search_runs_complete_chain(container):
    result = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id="u1", session_id="s1", content="推荐适合通勤的降噪耳机")
        )
    )
    assert result.intent.intent == Intent.RECOMMENDATION
    assert "AirBeat Pro" in result.answer
    assert result.tools_used == ["product.search"]
    assert result.need_human is False


def test_follow_up_uses_product_from_memory(container):
    asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id="u1", session_id="s1", content="SKU-1001 的规格参数")
        )
    )
    result = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id="u1", session_id="s1", content="它有库存吗")
        )
    )
    assert result.intent.intent == Intent.STOCK_QUERY
    assert "库存 126 件" in result.answer


def test_memory_is_isolated_by_user(container):
    asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id="alice", session_id="same", content="SKU-1001 的详情")
        )
    )
    bob = asyncio.run(container.memory.get("bob", "same"))
    assert bob.messages == []
    assert "product_id" not in bob.context


def test_concurrent_turns_do_not_overwrite_session_history(container):
    async def exercise():
        await asyncio.gather(
            container.orchestrator.handle(
                ChatMessage(user_id="u1", session_id="concurrent", content="你好")
            ),
            container.orchestrator.handle(
                ChatMessage(user_id="u1", session_id="concurrent", content="推荐通勤耳机")
            ),
        )
        return await container.memory.get("u1", "concurrent")

    memory = asyncio.run(exercise())
    assert len(memory.messages) == 4
    assert {message.content for message in memory.messages if message.role == "user"} == {
        "你好",
        "推荐通勤耳机",
    }
