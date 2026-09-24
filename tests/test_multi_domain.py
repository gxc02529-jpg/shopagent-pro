import asyncio

from shopagent.container import build_container
from shopagent.domain.models import ChatMessage, Intent
from shopagent.settings import Settings


def ask(container, content: str, *, user_id: str = "demo-user", session_id: str = "s1"):
    return asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id=user_id, session_id=session_id, content=content)
        )
    )


def test_order_logistics_and_follow_up():
    container = build_container(Settings())
    result = ask(container, "查询订单 ORD-20260001 的物流")
    assert result.intent.intent == Intent.ORDER_QUERY
    assert "上海分拨中心" in result.answer
    assert result.tools_used == ["order.logistics"]

    follow_up = ask(container, "这个订单现在什么状态")
    assert "运输中" in follow_up.answer
    assert follow_up.tools_used == ["order.detail"]


def test_order_cannot_be_read_by_another_user():
    container = build_container(Settings())
    result = ask(container, "查询订单 ORD-20260001", user_id="other-user")
    assert "不属于当前用户" in result.answer
    assert result.data["order"] is None


def test_create_after_sales_ticket_only_for_owned_order():
    container = build_container(Settings())
    result = ask(container, "订单 ORD-20260002 的背包有破损，我要退货")
    assert result.intent.intent == Intent.AFTER_SALES
    assert "已创建退货工单" in result.answer
    assert result.data["ticket"]["user_id"] == "demo-user"

    forbidden = ask(
        container,
        "订单 ORD-20260002 的商品有问题，我要换货",
        user_id="other-user",
        session_id="s2",
    )
    assert forbidden.data["error"] == "order_not_found_or_forbidden"
    assert forbidden.need_human is True


def test_refund_status_does_not_create_ticket():
    container = build_container(Settings())
    result = ask(container, "ORD-20260002 的退款进度怎么样")
    assert result.intent.intent == Intent.ORDER_QUERY
    assert "退款处理中" in result.answer
    assert result.tools_used == ["order.refund"]


def test_recommendation_agent_applies_budget():
    container = build_container(Settings())
    result = ask(container, "推荐预算 500 以内适合通勤的耳机")
    assert result.intent.intent == Intent.RECOMMENDATION
    assert result.data["budget"] == 500
    assert all(item["price"] <= 500 for item in result.data["products"])
    assert "AirBeat Lite" in result.answer
