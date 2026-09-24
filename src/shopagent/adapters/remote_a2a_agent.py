from __future__ import annotations

from uuid import uuid4

from shopagent.domain.models import AgentResult, ChatMessage, IntentResult, SessionMemory
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

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        import httpx

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
                    "memory": memory.model_dump(mode="json"),
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
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
