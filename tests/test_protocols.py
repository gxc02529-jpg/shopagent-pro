from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.settings import Settings


def client() -> TestClient:
    return TestClient(create_app(Settings()))


def test_mcp_initialize_list_and_call_tool():
    api = client()
    initialized = api.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    ).json()
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    assert "tools" in initialized["result"]["capabilities"]

    listed = api.post(
        "/mcp",
        headers={"X-Agent-Name": "product_agent", "MCP-Protocol-Version": "2025-11-25"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == {
        "product.search",
        "product.detail",
        "product.stock",
        "knowledge.search",
    }
    assert listed["result"]["tools"][0]["inputSchema"]["type"] == "object"

    called = api.post(
        "/mcp",
        headers={"X-Agent-Name": "product_agent", "MCP-Protocol-Version": "2025-11-25"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "product.stock", "arguments": {"product_id": "SKU-1001"}},
        },
    ).json()
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["stock"] == 126


def test_mcp_enforces_agent_tool_scope():
    api = client()
    response = api.post(
        "/mcp",
        headers={"X-Agent-Name": "order_agent"},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "product.stock", "arguments": {"product_id": "SKU-1001"}},
        },
    ).json()
    assert response["error"]["code"] == -32602


def test_malformed_mcp_calls_do_not_trip_tool_circuit():
    api = client()
    malformed = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "product.stock", "arguments": {"wrong": "SKU-1001"}},
    }
    for _ in range(5):
        response = api.post(
            "/mcp", headers={"X-Agent-Name": "product_agent"}, json=malformed
        ).json()
        assert response["error"]["code"] == -32602
    healthy = api.post(
        "/mcp",
        headers={"X-Agent-Name": "product_agent"},
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "product.stock", "arguments": {"product_id": "SKU-1001"}},
        },
    ).json()
    assert healthy["result"]["structuredContent"]["stock"] == 126


def test_a2a_agent_card_send_and_task_polling():
    api = client()
    card = api.get("/.well-known/agent-card.json").json()
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert {skill["id"] for skill in card["skills"]} >= {"recommendation", "order_query"}

    sent = api.post(
        "/a2a/message:send",
        headers={"Content-Type": "application/a2a+json", "A2A-Version": "1.0"},
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "msg-1",
                "contextId": "ctx-1",
                "parts": [{"text": "推荐预算 500 以内适合通勤的耳机"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    )
    assert sent.headers["content-type"].startswith("application/a2a+json")
    task = sent.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["metadata"]["delegatedAgent"] == "recommendation_agent"
    assert api.get(f"/a2a/tasks/{task['id']}").json()["id"] == task["id"]


def test_a2a_task_persisted_to_operations_store_and_retrievable():
    api = client()
    sent = api.post(
        "/a2a/message:send",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "msg-persist",
                "parts": [{"text": "推荐预算 300 以内适合通勤的耳机"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    ).json()["task"]
    assert sent["status"]["state"] == "TASK_STATE_COMPLETED"

    # A2A task polling now resolves through the durable operations store.
    lookup = api.get(f"/a2a/tasks/{sent['id']}").json()
    assert lookup["id"] == sent["id"]
    assert lookup["artifacts"] == sent["artifacts"]

    # Unknown task id yields 404.
    assert api.get("/a2a/tasks/does-not-exist").status_code == 404


def test_metrics_exposes_business_level_counters():
    api = client()
    # Generate at least one interaction so the dashboard has data.
    api.post(
        "/a2a/message:send",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "msg-metrics",
                "parts": [{"text": "推荐一款适合通勤的耳机"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    )
    body = api.get("/metrics").text
    assert "shopagent_interactions_total" in body
    assert "shopagent_intent_total" in body
    assert "shopagent_automation_rate" in body
    assert "shopagent_pending_knowledge_candidates" in body
    assert "shopagent_tool_calls_total" in body


def test_a2a_domain_agent_rejects_wrongly_routed_work():
    api = client()
    response = api.post(
        "/a2a/order_agent/message:send",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "msg-2",
                "parts": [{"text": "SKU-1001 的库存"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    ).json()
    task = response["task"]
    assert task["status"]["state"] == "TASK_STATE_REJECTED"
    assert task["metadata"]["delegatedAgent"] == "product_agent"


def test_a2a_wrong_target_is_rejected_before_side_effect():
    api = client()
    rejected = api.post(
        "/a2a/product_agent/message:send",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "must-not-create-ticket",
                "contextId": "side-effect-guard",
                "parts": [{"text": "帮我为订单 ORD-20260002 申请退货"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    ).json()["task"]
    assert rejected["status"]["state"] == "TASK_STATE_REJECTED"
    assert "AS-" not in str(rejected)

    lookup = api.post(
        "/a2a/after_sales_agent/message:send",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "lookup-after-rejection",
                "contextId": "side-effect-guard",
                "parts": [{"text": "查询工单 AS-MUSTNOTEXIST 的进度"}],
                "metadata": {"userId": "demo-user"},
            }
        },
    ).json()["task"]
    assert "没有找到该工单" in lookup["artifacts"][0]["parts"][0]["text"]
