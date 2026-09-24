import asyncio
import re

from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.container import build_container
from shopagent.domain.models import ChatMessage
from shopagent.settings import Settings


def test_after_sales_create_then_track_closes_execution_loop():
    container = build_container(Settings())
    created = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="ticket-loop",
                content="帮我为订单 ORD-20260002 申请换货",
            )
        )
    )
    ticket_id = re.search(r"AS-[A-Z0-9]+", created.answer).group(0)
    tracked = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="ticket-loop",
                content=f"工单 {ticket_id} 处理到哪了",
            )
        )
    )
    assert tracked.tools_used == ["after_sales.get"]
    assert "当前状态：已创建" in tracked.answer


def test_negative_feedback_review_publish_and_reuse_loop():
    settings = Settings(admin_token="test-admin")
    api = TestClient(create_app(settings))
    question = "运费险最多多久完成理赔？"
    chat = api.post(
        "/api/v1/chat",
        json={
            "channel": "web",
            "user_id": "demo-user",
            "session_id": "feedback-loop",
            "content": question,
        },
    ).json()
    feedback = api.post(
        "/api/v1/feedback",
        json={
            "trace_id": chat["trace_id"],
            "user_id": "demo-user",
            "rating": 1,
            "comment": "缺少明确时效",
            "suggested_answer": "运费险理赔通常在退货签收后 72 小时内完成审核，最终以保险服务结果为准。",
        },
    )
    assert feedback.status_code == 201
    candidate = feedback.json()["knowledge_candidate"]
    assert candidate["status"] == "pending"

    assert api.get("/api/v1/admin/dashboard", headers={"X-Admin-Token": "wrong"}).status_code == 401
    headers = {"X-Admin-Token": "test-admin", "X-Admin-User": "knowledge-reviewer"}
    reviewed = api.post(
        f"/api/v1/admin/knowledge-candidates/{candidate['id']}/review",
        headers=headers,
        json={"approved": True},
    )
    assert reviewed.json()["status"] == "published"

    reused = api.post(
        "/api/v1/chat",
        json={
            "channel": "web",
            "user_id": "demo-user",
            "session_id": "feedback-loop-2",
            "content": question,
        },
    ).json()
    assert "72 小时" in reused["answer"]
    assert "客服反馈审核" in reused["answer"]

    dashboard = api.get("/api/v1/admin/dashboard", headers=headers).json()
    assert dashboard["total_interactions"] == 2
    assert dashboard["feedback_count"] == 1
    assert dashboard["pending_knowledge_candidates"] == 0
    assert dashboard["average_rating"] == 1.0


def test_feedback_is_bound_to_interaction_owner():
    api = TestClient(create_app(Settings()))
    chat = api.post(
        "/api/v1/chat",
        json={"channel": "web", "user_id": "alice", "session_id": "s1", "content": "你好"},
    ).json()
    response = api.post(
        "/api/v1/feedback",
        json={"trace_id": chat["trace_id"], "user_id": "bob", "rating": 1},
    )
    assert response.status_code == 403


def test_reviewing_same_candidate_twice_is_rejected():
    api = TestClient(create_app(Settings(admin_token="test-admin")))
    chat = api.post(
        "/api/v1/chat",
        json={
            "channel": "web",
            "user_id": "demo-user",
            "session_id": "dup-review",
            "content": "运费险怎么理赔",
        },
    ).json()
    feedback = api.post(
        "/api/v1/feedback",
        json={
            "trace_id": chat["trace_id"],
            "user_id": "demo-user",
            "rating": 1,
            "comment": "缺时效",
        },
    ).json()
    candidate_id = feedback["knowledge_candidate"]["id"]
    headers = {"X-Admin-Token": "test-admin", "X-Admin-User": "reviewer"}
    first = api.post(
        f"/api/v1/admin/knowledge-candidates/{candidate_id}/review",
        headers=headers,
        json={"approved": True},
    )
    assert first.status_code == 200
    second = api.post(
        f"/api/v1/admin/knowledge-candidates/{candidate_id}/review",
        headers=headers,
        json={"approved": True},
    )
    assert second.status_code == 409


def test_concurrent_review_only_one_succeeds():
    container = build_container(Settings())

    async def setup_trace() -> str:
        result = await container.orchestrator.handle(
            ChatMessage(user_id="demo-user", session_id="conc-review", content="退货条件是什么")
        )
        return result.trace_id

    trace_id = asyncio.run(setup_trace())
    _, candidate = asyncio.run(
        container.feedback.submit(
            trace_id=trace_id, user_id="demo-user", rating=1, comment="不清楚"
        )
    )

    async def attempt() -> str:
        try:
            await container.feedback.review(candidate.id, approved=True, reviewer="r")
            return "ok"
        except ValueError:
            return "rejected"

    async def run_all() -> list[str]:
        return list(await asyncio.gather(*[attempt() for _ in range(5)]))

    results = asyncio.run(run_all())
    assert results.count("ok") == 1
    assert results.count("rejected") == 4


def test_failed_knowledge_publish_is_audited_and_can_be_retried():
    container = build_container(Settings())

    class FlakyKnowledge:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self.calls = 0

        async def upsert(self, document) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated knowledge outage")
            await self.delegate.upsert(document)

    flaky = FlakyKnowledge(container.knowledge_repository)
    container.feedback._knowledge = flaky

    async def exercise():
        result = await container.orchestrator.handle(
            ChatMessage(user_id="demo-user", session_id="retry-publish", content="退货条件是什么")
        )
        _, candidate = await container.feedback.submit(
            trace_id=result.trace_id,
            user_id="demo-user",
            rating=1,
            comment="答案需要改进",
        )
        try:
            await container.feedback.review(candidate.id, approved=True, reviewer="reviewer")
        except RuntimeError:
            pass
        failed = await container.operations.get_candidate(candidate.id)
        retried = await container.feedback.review(candidate.id, approved=True, reviewer="reviewer")
        events = await container.operations.list_audit()
        return failed, retried, events

    failed, retried, events = asyncio.run(exercise())
    assert failed.status == "publish_failed"
    assert retried.status == "published"
    assert "knowledge.publish_failed" in {event.event_type for event in events}


def test_reviewed_product_feedback_is_reused_by_product_agent():
    api = TestClient(create_app(Settings(admin_token="test-admin")))
    question = "商品 火星露营炉怎么使用？"
    first = api.post(
        "/api/v1/chat",
        json={
            "channel": "web",
            "user_id": "demo-user",
            "session_id": "product-feedback-1",
            "content": question,
        },
    ).json()
    feedback = api.post(
        "/api/v1/feedback",
        json={
            "trace_id": first["trace_id"],
            "user_id": "demo-user",
            "rating": 1,
            "comment": "缺少说明",
            "suggested_answer": "火星露营炉应在通风的户外环境使用，并远离可燃物。",
        },
    ).json()
    candidate_id = feedback["knowledge_candidate"]["id"]
    reviewed = api.post(
        f"/api/v1/admin/knowledge-candidates/{candidate_id}/review",
        headers={"X-Admin-Token": "test-admin"},
        json={"approved": True},
    )
    assert reviewed.status_code == 200

    reused = api.post(
        "/api/v1/chat",
        json={
            "channel": "web",
            "user_id": "demo-user",
            "session_id": "product-feedback-2",
            "content": question,
        },
    ).json()
    assert "通风的户外环境" in reused["answer"]
    assert reused["tools_used"] == ["knowledge.search"]
