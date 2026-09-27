import asyncio

from shopagent.container import build_container
from shopagent.domain.models import ChatMessage
from shopagent.settings import Settings


def ask(content: str):
    container = build_container(Settings())
    return asyncio.run(
        container.orchestrator.handle(
            ChatMessage(user_id="demo-user", session_id="planning-session", content=content)
        )
    )


def test_independent_multi_domain_questions_run_as_parallel_plan():
    result = ask("推荐预算500以内适合通勤的耳机，再帮我查询订单 ORD-20260001 的物流")

    assert result.routing_decision == "multi_agent_parallel"
    assert result.routed_agent == "multi_agent"
    assert result.data["plan_type"] == "parallel"
    assert result.data["task_count"] == 2
    assert {item["routed_agent"] for item in result.data["tasks"]} == {
        "recommendation_agent",
        "order_agent",
    }
    assert set(result.tools_used) == {"product.search", "order.logistics"}
    assert "AirBeat Lite" in result.answer
    assert "上海分拨中心" in result.answer
    assert result.need_human is False


def test_parallel_plan_reaches_both_agents_before_either_finishes():
    async def exercise():
        container = build_container(Settings())
        recommendation = container.agents.get("recommendation_agent")
        order = container.agents.get("order_agent")
        assert recommendation is not None and order is not None
        recommendation_execute = recommendation.execute
        order_execute = order.execute
        started = 0
        both_started = asyncio.Event()

        async def wait_for_peer(execute, *args):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return await execute(*args)

        async def delayed_recommendation(*args):
            return await wait_for_peer(recommendation_execute, *args)

        async def delayed_order(*args):
            return await wait_for_peer(order_execute, *args)

        recommendation.execute = delayed_recommendation
        order.execute = delayed_order
        result = await container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="parallel-proof",
                content="推荐预算500以内适合通勤的耳机，再帮我查询订单 ORD-20260001 的物流",
            )
        )
        return started, result

    started, result = asyncio.run(exercise())
    assert started == 2
    assert result.routing_decision == "multi_agent_parallel"
    assert result.need_human is False


def test_one_failed_parallel_task_preserves_other_successful_result():
    async def exercise():
        container = build_container(Settings())
        order = container.agents.get("order_agent")
        assert order is not None

        async def unavailable(*_args):
            raise TimeoutError("simulated downstream timeout")

        order.execute = unavailable
        return await container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="partial-success",
                content="推荐预算500以内适合通勤的耳机，再帮我查询订单 ORD-20260001 的物流",
            )
        )

    result = asyncio.run(exercise())
    states = {item["routed_agent"]: item["state"] for item in result.data["tasks"]}
    assert states == {"recommendation_agent": "completed", "order_agent": "failed"}
    assert result.tools_used == ["product.search"]
    assert "AirBeat Lite" in result.answer
    assert "其他问题不受影响" in result.answer
    assert result.need_human is True


def test_conditional_complex_question_skips_action_when_condition_is_false():
    result = ask("查询订单 ORD-20260001 的物流，如果还没发货就帮我申请退款")

    assert result.routing_decision == "complex_task_sequential"
    assert result.data["plan_type"] == "sequential"
    assert result.data["tasks"][0]["state"] == "completed"
    assert result.data["tasks"][1]["state"] == "skipped"
    assert result.data["tasks"][1]["depends_on"] == ["task-1"]
    assert result.tools_used == ["order.logistics"]
    assert "条件“还没发货”不成立" in result.answer
    assert result.need_human is False


def test_unverifiable_condition_does_not_execute_follow_up_action():
    result = ask("查询订单 ORD-20260001 的物流，如果客户着急就帮我申请退款")

    assert result.data["tasks"][1]["state"] == "input_required"
    assert "暂时无法可靠判断条件" in result.answer
    assert "after_sales.create" not in result.tools_used
    assert result.need_human is True
