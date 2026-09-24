import asyncio
import re

from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.container import build_container
from shopagent.domain.models import ChatMessage
from shopagent.security.redaction import redact_sensitive_text
from shopagent.settings import Settings


def test_sensitive_text_redaction():
    value = redact_sensitive_text(
        "手机号13812345678，邮箱alice@example.com，订单ORD-20260001，运单123456789012"
    )
    assert "138****5678" in value
    assert "a***@example.com" in value
    assert "ORD-****0001" in value
    assert "123****9012" in value
    assert "13812345678" not in value


def test_interaction_persistence_uses_redacted_content():
    container = build_container(Settings())
    response = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="u1",
                session_id="s1",
                content="订单 ORD-20260001，联系邮箱 alice@example.com",
            )
        )
    )
    record = asyncio.run(container.operations.get_interaction(response.trace_id))
    assert "alice@example.com" not in record.question
    assert "a***@example.com" in record.question
    assert "ORD-****0001" in record.question


def test_after_sales_write_is_idempotent():
    api = TestClient(create_app(Settings()))
    payload = {
        "channel": "web",
        "user_id": "demo-user",
        "session_id": "idempotent-ticket",
        "content": "帮我为订单 ORD-20260002 申请换货",
    }
    headers = {"Idempotency-Key": "ticket-request-001"}
    first = api.post("/api/v1/chat", json=payload, headers=headers).json()
    second = api.post("/api/v1/chat", json=payload, headers=headers).json()
    first_id = re.search(r"AS-[A-Z0-9]+", first["answer"]).group(0)
    second_id = re.search(r"AS-[A-Z0-9]+", second["answer"]).group(0)
    assert first_id == second_id


def test_feedback_retry_returns_same_record_and_single_audit_event():
    settings = Settings(admin_token="audit-admin")
    api = TestClient(create_app(settings))
    chat = api.post(
        "/api/v1/chat",
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": "你好"},
    ).json()
    payload = {"trace_id": chat["trace_id"], "user_id": "u1", "rating": 1, "comment": "需改进"}
    headers = {"Idempotency-Key": "feedback-request-001"}
    first = api.post("/api/v1/feedback", json=payload, headers=headers).json()
    second = api.post("/api/v1/feedback", json=payload, headers=headers).json()
    assert first["feedback"]["id"] == second["feedback"]["id"]
    admin = {"X-Admin-Token": "audit-admin"}
    dashboard = api.get("/api/v1/admin/dashboard", headers=admin).json()
    audits = api.get("/api/v1/admin/audit-events", headers=admin).json()
    assert dashboard["feedback_count"] == 1
    assert dashboard["audit_event_count"] == 1
    assert audits[0]["event_type"] == "feedback.submitted"


def test_concurrent_feedback_submission_is_atomically_idempotent():
    container = build_container(Settings())

    async def exercise() -> tuple[list[str], dict]:
        response = await container.orchestrator.handle(
            ChatMessage(user_id="u1", session_id="race", content="你好")
        )

        async def submit_once() -> str:
            feedback, _ = await container.feedback.submit(
                trace_id=response.trace_id,
                user_id="u1",
                rating=4,
                idempotency_key="same-feedback-request",
            )
            return feedback.id

        identifiers = list(await asyncio.gather(*(submit_once() for _ in range(20))))
        return identifiers, await container.operations.dashboard()

    identifiers, dashboard = asyncio.run(exercise())
    assert len(set(identifiers)) == 1
    assert dashboard["feedback_count"] == 1
    assert dashboard["audit_event_count"] == 1
