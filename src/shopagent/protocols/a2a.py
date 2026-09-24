from __future__ import annotations

import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from shopagent.agents.base import AgentRegistry
from shopagent.domain.models import ChatMessage, IntentResult, SessionMemory
from shopagent.orchestration.service import ShopAgentOrchestrator
from shopagent.ports.operations import OperationsStore

logger = logging.getLogger(__name__)


class A2AServerAdapter:
    """A2A 1.0 HTTP+JSON binding for discovery, messages, tasks and artifacts."""

    def __init__(
        self,
        orchestrator: ShopAgentOrchestrator,
        agents: AgentRegistry,
        operations: OperationsStore,
        task_ttl_seconds: int = 3600,
    ) -> None:
        self._orchestrator = orchestrator
        self._agents = agents
        self._operations = operations
        self._task_ttl_seconds = task_ttl_seconds

    def agent_card(self, base_url: str, agent_name: str | None = None) -> dict[str, Any]:
        if agent_name:
            descriptor = self._agents.descriptor(agent_name)
            if not descriptor:
                raise KeyError(agent_name)
            skills = [
                {
                    "id": intent.value,
                    "name": intent.value.replace("_", " ").title(),
                    "description": descriptor.description,
                    "tags": ["ecommerce", intent.value],
                    "examples": [f"请处理 {intent.value} 请求"],
                }
                for intent in descriptor.intents
            ]
            name, description = descriptor.name, descriptor.description
            endpoint = f"{base_url}/a2a/{agent_name}"
        else:
            skills = [
                skill
                for item in self._agents.describe()
                for skill in self._skills_from_descriptor(item)
            ]
            name = "shopagent_orchestrator"
            description = "ShopAgent Pro 多领域电商客服编排 Agent"
            endpoint = f"{base_url}/a2a"
        return {
            "name": name,
            "description": description,
            "supportedInterfaces": [
                {"url": endpoint, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}
            ],
            "provider": {"organization": "ShopAgent Pro", "url": base_url},
            "version": "1.0.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": skills,
        }

    async def send_message(
        self,
        payload: dict[str, Any],
        *,
        target_agent: str | None = None,
        owner: str = "anonymous",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        message = payload.get("message") or {}
        content = self._text_content(message.get("parts") or [])
        if not content:
            raise ValueError("message.parts must contain a non-empty text part")
        metadata = message.get("metadata") or payload.get("metadata") or {}
        user_id = str(metadata.get("userId") or "a2a-user")
        context_id = str(message.get("contextId") or payload.get("contextId") or uuid4().hex)
        # IDs are always server generated. Accepting caller-selected IDs allowed
        # one caller to overwrite another caller's task in shared storage.
        task_id = uuid4().hex
        trace_id = trace_id or str(metadata.get("traceId") or uuid4().hex)
        delegated_context = metadata.get("messageContext")
        delegated_context = delegated_context if isinstance(delegated_context, dict) else {}
        chat_message = ChatMessage(
            channel="web",
            user_id=user_id,
            session_id=context_id,
            content=content,
            context={
                **delegated_context,
                "protocol": "a2a",
                "message_id": message.get("messageId"),
                "trace_id": trace_id,
            },
        )
        classified_intent = await self._orchestrator.classify(content)
        delegated = self._agents.resolve(classified_intent.intent)
        delegated_name = delegated.name if delegated else None
        if target_agent and target_agent != delegated_name:
            task = self._task(
                task_id,
                context_id,
                state="TASK_STATE_REJECTED",
                answer=f"请求意图应由 {delegated_name or '人工'} 处理，不能交给 {target_agent}。",
                result={
                    "intent": classified_intent.model_dump(mode="json"),
                    "routed_agent": delegated_name,
                    "tools_used": [],
                    "need_human": True,
                },
                delegated_agent=delegated_name,
                trace_id=trace_id,
            )
        elif target_agent:
            agent = self._agents.get(target_agent)
            if agent is None:
                raise ValueError("target agent is not registered")
            raw_intent = metadata.get("intent")
            intent = (
                IntentResult.model_validate(raw_intent)
                if isinstance(raw_intent, dict)
                else classified_intent
            )
            raw_memory = metadata.get("memory")
            try:
                memory = (
                    SessionMemory.model_validate(raw_memory)
                    if isinstance(raw_memory, dict)
                    else SessionMemory(user_id=user_id, session_id=context_id)
                )
            except ValueError:
                memory = SessionMemory(user_id=user_id, session_id=context_id)
            if memory.user_id != user_id or memory.session_id != context_id:
                raise ValueError("delegated memory scope does not match the A2A message")
            try:
                result = await agent.execute(chat_message, intent, memory)
            except Exception:
                logger.exception(
                    "A2A domain execution failed: trace_id=%s agent=%s", trace_id, target_agent
                )
                task = self._task(
                    task_id,
                    context_id,
                    state="TASK_STATE_FAILED",
                    answer="领域服务暂时不可用，请稍后重试或转人工处理。",
                    result={"error": "agent_unavailable", "degraded": True},
                    delegated_agent=target_agent,
                    trace_id=trace_id,
                )
            else:
                task = self._task(
                    task_id,
                    context_id,
                    state="TASK_STATE_COMPLETED",
                    answer=result.answer,
                    result=result.model_dump(mode="json"),
                    delegated_agent=target_agent,
                    trace_id=trace_id,
                )
        else:
            result = await self._orchestrator.handle(chat_message, trace_id)
            task = self._task(
                task_id,
                context_id,
                state="TASK_STATE_COMPLETED",
                answer=result.answer,
                result=result.model_dump(mode="json"),
                delegated_agent=delegated_name,
                trace_id=trace_id,
            )
        await self._operations.save_task(
            task,
            owner=owner,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._task_ttl_seconds),
        )
        return {"task": deepcopy(task)}

    async def get_task(self, task_id: str, *, owner: str = "anonymous") -> dict[str, Any] | None:
        return await self._operations.get_task(task_id, owner=owner)

    @staticmethod
    def _text_content(parts: list[dict[str, Any]]) -> str:
        return "\n".join(str(part["text"]) for part in parts if part.get("text")).strip()

    @staticmethod
    def _skills_from_descriptor(item: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": intent,
                "name": intent.replace("_", " ").title(),
                "description": item["description"],
                "tags": ["ecommerce", intent],
            }
            for intent in item["intents"]
        ]

    @staticmethod
    def _task(
        task_id: str,
        context_id: str,
        *,
        state: str,
        answer: str,
        result: dict[str, Any],
        delegated_agent: str | None,
        trace_id: str,
    ) -> dict[str, Any]:
        return {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": state},
            "artifacts": [
                {
                    "artifactId": f"artifact-{uuid4().hex[:12]}",
                    "name": "ShopAgent result",
                    "parts": [{"text": answer}, {"data": result}],
                }
            ],
            "metadata": {"delegatedAgent": delegated_agent, "traceId": trace_id},
        }
