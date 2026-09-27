from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

from shopagent.domain.models import (
    AgentResult,
    ChatMessage,
    DelegatedMemory,
    IntentResult,
    SessionMemory,
)
from shopagent.security.tokens import issue_token


class RemoteA2AAgent:
    """Drop-in Agent implementation for moving a domain Agent to another service."""

    def __init__(
        self,
        name: str,
        endpoint: str,
        *,
        service_secret: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.name = name
        self._endpoint = endpoint.rstrip("/")
        self._service_secret = service_secret
        self._timeout = timeout_seconds
        self._client = None

    def _http_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                trust_env=False,
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
            )
        return self._client

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        memory_ref = (
            "memory://"
            + hmac.new(
                self._service_secret.encode(),
                f"{memory.user_id}\0{memory.session_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        delegated_memory = DelegatedMemory(
            memory_ref=memory_ref,
            user_id=memory.user_id,
            session_id=memory.session_id,
            product_id=memory.context.get("product_id"),
            order_id=memory.context.get("order_id"),
        )
        payload = {
            "message": {
                "role": "ROLE_USER",
                "messageId": uuid4().hex,
                "contextId": message.session_id,
                "parts": [{"text": message.content}],
                "metadata": {
                    "userId": message.user_id,
                    "delegated": True,
                    "traceId": message.context.get("trace_id"),
                    "intent": intent.model_dump(mode="json"),
                    # Do not copy the full conversation across the A2A boundary.
                    # Domain agents receive only an opaque reference and fields needed
                    # to execute this task.
                    "delegatedMemory": delegated_memory.model_dump(mode="json"),
                    "messageContext": message.context,
                },
            }
        }
        token = issue_token(
            self._service_secret,
            subject="shopagent_orchestrator",
            role="service",
            agent=self.name,
            ttl_seconds=60,
        )
        response = await self._http_client().post(
            f"{self._endpoint}/message:send",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/a2a+json",
                "A2A-Version": "1.0",
            },
        )
        response.raise_for_status()
        task = response.json()["task"]
        artifact = task["artifacts"][0]
        answer = next(part["text"] for part in artifact["parts"] if "text" in part)
        data = next((part["data"] for part in artifact["parts"] if "data" in part), {})
        return AgentResult(
            answer=answer,
            data=data.get("data", data),
            tools_used=data.get("tools_used", []),
            degraded=task["status"]["state"] != "TASK_STATE_COMPLETED",
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
