from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.settings import Settings


def test_health_and_ready():
    client = TestClient(create_app(Settings()))
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert {tool["name"] for tool in ready["tools"]["product_agent"]} == {
        "product.search",
        "product.detail",
        "product.stock",
        "knowledge.search",
    }


def test_chat_and_trace_header():
    client = TestClient(create_app(Settings()))
    response = client.post(
        "/api/v1/chat",
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": "SKU-1001 有货吗"},
    )
    assert response.status_code == 200
    assert response.headers["x-trace-id"] == response.json()["trace_id"]
    assert response.json()["data"]["stock"] == 126
    assert response.json()["routing_decision"] == "agent_delegated"
    assert response.json()["routing_threshold"] == 0.80


def test_trace_id_is_server_owned_and_correlation_id_is_echoed_separately():
    client = TestClient(create_app(Settings()))
    response = client.post(
        "/api/v1/chat",
        headers={"X-Trace-ID": "attacker-selected", "X-Correlation-ID": "client-order-42"},
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": "你好"},
    )
    assert response.status_code == 200
    assert response.headers["x-trace-id"] != "attacker-selected"
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
    assert response.headers["x-correlation-id"] == "client-order-42"


def test_stream_contains_typed_events():
    client = TestClient(create_app(Settings()))
    response = client.post(
        "/api/v1/chat/stream",
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": "你好"},
    )
    assert response.status_code == 200
    assert "event: trace" in response.text
    assert "event: intent" in response.text
    assert "event: message" in response.text
    assert "event: done" in response.text


def test_metrics_and_agent_discovery():
    client = TestClient(create_app(Settings()))
    client.post(
        "/api/v1/chat",
        json={"channel": "web", "user_id": "u1", "session_id": "s1", "content": "SKU-1001 有货吗"},
    )
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert 'shopagent_tool_calls_total{tool="product_stock"} 1' in metrics.text
    assert "shopagent_human_handoff_rate 0.0" in metrics.text
    assert 'shopagent_agent_routed_total{agent="product_agent"} 1' in metrics.text
    agents = client.get("/ready").json()["agents"]
    assert {item["name"] for item in agents} == {
        "product_agent",
        "recommendation_agent",
        "order_agent",
        "after_sales_agent",
    }
