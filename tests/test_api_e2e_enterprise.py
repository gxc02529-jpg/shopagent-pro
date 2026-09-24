"""End-to-end acceptance flow: chat -> trace -> feedback -> knowledge candidate -> admin review.

Covers the enterprise closed loop (feedback ingestion, PII redaction, idempotency,
admin auth, candidate review and audit trail) through the real HTTP surface.
"""

from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.settings import Settings

ADMIN_TOKEN = "test-admin-token-for-acceptance"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN, "X-Admin-User": "alice"}


def _client() -> TestClient:
    return TestClient(create_app(Settings(admin_token=ADMIN_TOKEN)))


def _chat(client: TestClient, content: str = "SKU-1001 有货吗") -> str:
    response = client.post(
        "/api/v1/chat",
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": content},
    )
    assert response.status_code == 200
    return response.json()["trace_id"]


def test_health_ready_and_metrics_are_reachable():
    client = _client()
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "shopagent_interactions_total" in metrics.text


def test_feedback_knowledge_review_closed_loop():
    client = _client()
    trace_id = _chat(client)

    # Low rating (<=2) must generate a pending knowledge candidate.
    response = client.post(
        "/api/v1/feedback",
        json={
            "trace_id": trace_id,
            "user_id": "u1",
            "rating": 1,
            "comment": "答案不对，联系我 13800001111",
            "suggested_answer": "应回答：支持七天无理由退货",
        },
        headers={"Idempotency-Key": "fb-acceptance-1"},
    )
    assert response.status_code == 201
    body = response.json()
    candidate_id = body["knowledge_candidate"]["id"]
    assert body["knowledge_candidate"]["status"] == "pending"
    # PII must be masked before persistence.
    assert "13800001111" not in body["feedback"]["comment"]
    assert "138****1111" in body["feedback"]["comment"]

    # Replaying the same idempotency key returns the original record.
    replay = client.post(
        "/api/v1/feedback",
        json={"trace_id": trace_id, "user_id": "u1", "rating": 1},
        headers={"Idempotency-Key": "fb-acceptance-1"},
    )
    assert replay.status_code == 201
    assert replay.json()["feedback"]["id"] == body["feedback"]["id"]

    # Admin routes are closed without a valid token.
    assert client.get("/api/v1/admin/dashboard").status_code == 401
    assert (
        client.get("/api/v1/admin/dashboard", headers={"X-Admin-Token": "wrong"}).status_code == 401
    )

    board = client.get("/api/v1/admin/dashboard", headers=ADMIN_HEADERS).json()
    assert board["total_interactions"] >= 1
    assert board["pending_knowledge_candidates"] >= 1

    pending = client.get(
        "/api/v1/admin/knowledge-candidates",
        params={"status": "pending"},
        headers=ADMIN_HEADERS,
    ).json()
    assert any(item["id"] == candidate_id for item in pending)

    # Approval publishes the answer back into the knowledge base.
    reviewed = client.post(
        f"/api/v1/admin/knowledge-candidates/{candidate_id}/review",
        json={"approved": True},
        headers=ADMIN_HEADERS,
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "published"
    assert reviewed.json()["reviewed_by"] == "alice"

    events = client.get("/api/v1/admin/audit-events", headers=ADMIN_HEADERS).json()
    event_types = {item["event_type"] for item in events}
    assert "feedback.submitted" in event_types
    assert "knowledge.published" in event_types

    # A second review of the same candidate conflicts.
    again = client.post(
        f"/api/v1/admin/knowledge-candidates/{candidate_id}/review",
        json={"approved": True},
        headers=ADMIN_HEADERS,
    )
    assert again.status_code == 409


def test_feedback_rejects_unknown_trace_and_wrong_owner():
    client = _client()
    unknown = client.post(
        "/api/v1/feedback",
        json={"trace_id": "missing-trace", "user_id": "u1", "rating": 5},
    )
    assert unknown.status_code == 404

    trace_id = _chat(client)
    wrong_owner = client.post(
        "/api/v1/feedback",
        json={"trace_id": trace_id, "user_id": "someone-else", "rating": 5},
    )
    assert wrong_owner.status_code == 403


def test_review_unknown_candidate_returns_404():
    client = _client()
    response = client.post(
        "/api/v1/admin/knowledge-candidates/KC-NOTEXIST/review",
        json={"approved": True},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404
