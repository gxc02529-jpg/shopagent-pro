import socket
import threading
import time

import httpx
import uvicorn

from shopagent.api import create_app
from shopagent.settings import Settings


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_chat_really_crosses_a2a_and_mcp_http_boundaries():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    settings = Settings(
        host="127.0.0.1",
        port=port,
        agent_transport="a2a",
        tool_transport="mcp",
        a2a_base_url=f"{base_url}/a2a",
        mcp_url=f"{base_url}/mcp",
        service_token="development-transport-secret-with-entropy-42",
    )
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started

    try:
        with httpx.Client(base_url=base_url, timeout=10) as client:
            response = client.post(
                "/api/v1/chat",
                json={
                    "channel": "web",
                    "user_id": "demo-user",
                    "session_id": "transport-e2e",
                    "content": "SKU-1001 有货吗",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["routed_agent"] == "product_agent"
            assert body["tools_used"] == ["product.stock"]
            assert body["data"]["stock"] == 126

            metrics = client.get("/metrics").text
            assert 'shopagent_tool_calls_total{tool="product_stock"} 1' in metrics
            assert "shopagent_a2a_tasks_total 1" in metrics
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
