import pytest
from fastapi.testclient import TestClient

from shopagent.api import create_app
from shopagent.container import build_container
from shopagent.security.tokens import issue_token
from shopagent.settings import Settings

ADMIN_SECRET = "Adm1n_4oZ7!qP2#xL9$vN6%bR3@tK8&cD5*"
SERVICE_SECRET = "Svc_8yW2!mN7#qR4$vX9%bK6@pT3&zL5*cD"
USER_SECRET = "Usr_6pQ9!vK3#xT8$mN2%bR7@yL4&zW5*cD"


def production_settings(**overrides):
    values = {
        "env": "production",
        "admin_token": ADMIN_SECRET,
        "service_token": SERVICE_SECRET,
        "user_token_secret": USER_SECRET,
        "cors_origins": "https://service.example.com",
        "memory_backend": "redis",
        "operations_backend": "mysql",
        "agent_transport": "a2a",
        "tool_transport": "mcp",
        "public_base_url": "https://service.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_rejects_default_secrets_and_wildcard_cors():
    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        Settings(env="production").validate_for_startup()
    with pytest.raises(ValueError, match="CORS"):
        production_settings(cors_origins="*").validate_for_startup()
    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        production_settings(
            admin_token="replace-with-at-least-32-random-characters"
        ).validate_for_startup()


def test_production_mcp_and_a2a_require_service_identity():
    container = build_container(Settings())
    api = TestClient(create_app(production_settings(), container=container))
    mcp_payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert api.post("/mcp", json=mcp_payload).status_code == 401
    assert api.get("/.well-known/agent-card.json").status_code == 401
    token = issue_token(
        SERVICE_SECRET,
        subject="product-agent-service",
        role="service",
        agent="product_agent",
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert api.post("/mcp", json=mcp_payload, headers=headers).status_code == 200
    assert api.get("/.well-known/agent-card.json", headers=headers).status_code == 200


def test_production_user_identity_is_bound_to_signed_subject():
    api = TestClient(create_app(production_settings(), container=build_container(Settings())))
    token = issue_token(USER_SECRET, subject="alice", role="user")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"channel": "web", "user_id": "bob", "session_id": "s1", "content": "你好"}
    assert api.post("/api/v1/chat", json=payload, headers=headers).status_code == 403
    payload["user_id"] = "alice"
    assert api.post("/api/v1/chat", json=payload, headers=headers).status_code == 200


def test_production_mcp_ignores_spoofed_agent_header():
    api = TestClient(create_app(production_settings(), container=build_container(Settings())))
    token = issue_token(
        SERVICE_SECRET,
        subject="order-service",
        role="service",
        agent="order_agent",
    )
    response = api.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Agent-Name": "product_agent",
        },
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "product.stock", "arguments": {"product_id": "SKU-1001"}},
        },
    )
    assert response.json()["error"]["code"] == -32602


def test_a2a_tasks_are_server_named_and_isolated_by_authenticated_owner():
    api = TestClient(create_app(production_settings(), container=build_container(Settings())))
    owner_one = issue_token(
        SERVICE_SECRET,
        subject="caller-one",
        role="service",
        agent="product_agent",
    )
    owner_two = issue_token(
        SERVICE_SECRET,
        subject="caller-two",
        role="service",
        agent="product_agent",
    )
    payload = {
        "message": {
            "role": "ROLE_USER",
            "messageId": "message-1",
            "taskId": "caller-controlled-id",
            "parts": [{"text": "SKU-1001 有货吗"}],
            "metadata": {"userId": "demo-user"},
        }
    }
    first = api.post(
        "/a2a/message:send",
        json=payload,
        headers={"Authorization": f"Bearer {owner_one}"},
    ).json()["task"]
    second = api.post(
        "/a2a/message:send",
        json=payload,
        headers={"Authorization": f"Bearer {owner_one}"},
    ).json()["task"]
    assert first["id"] != "caller-controlled-id"
    assert first["id"] != second["id"]
    path = f"/a2a/tasks/{first['id']}"
    assert api.get(path, headers={"Authorization": f"Bearer {owner_one}"}).status_code == 200
    assert api.get(path, headers={"Authorization": f"Bearer {owner_two}"}).status_code == 404


def test_security_headers_and_dependency_readiness():
    api = TestClient(create_app(Settings()))
    response = api.get("/ready")
    assert response.status_code == 200
    assert response.json()["storage"] == {
        "memory": True,
        "operations": True,
        "knowledge": True,
        "commerce": True,
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
