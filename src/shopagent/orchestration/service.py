from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from shopagent.agents.base import AgentRegistry
from shopagent.agents.intent import IntentAgent
from shopagent.domain.models import (
    ChatMessage,
    ChatResponse,
    Intent,
    IntentResult,
    InteractionRecord,
    MemoryMessage,
)
from shopagent.ports.llm import LLMProvider
from shopagent.ports.memory import MemoryStore
from shopagent.ports.operations import OperationsStore
from shopagent.security.guardrails import SAFE_DEFLECTION, detect_prompt_injection
from shopagent.security.redaction import redact_sensitive_text

logger = logging.getLogger(__name__)


class ShopAgentOrchestrator:
    def __init__(
        self,
        *,
        intent_agent: IntentAgent,
        agents: AgentRegistry,
        memory_store: MemoryStore,
        operations: OperationsStore,
        low_confidence_threshold: float = 0.55,
        llm: LLMProvider | None = None,
        guardrails_enabled: bool = False,
        redact_generated: bool = False,
    ) -> None:
        self._intent = intent_agent
        self._agents = agents
        self._memory = memory_store
        self._operations = operations
        self._threshold = low_confidence_threshold
        self._llm = llm
        self._guardrails_enabled = guardrails_enabled
        self._redact_generated = redact_generated

    async def classify(self, content: str):
        return await self._resolve_intent(content)

    async def _resolve_intent(self, content: str):
        if self._llm is not None:
            try:
                result = await self._llm.classify(content)
            except Exception:  # noqa: BLE001 - fall back to the rule intent engine
                result = None
            if result is not None:
                return result
        return await self._intent.classify(content)

    async def generate_answer(
        self, question: str, *, context: str = "", history: str = ""
    ) -> str | None:
        if self._llm is not None:
            try:
                return await self._llm.generate(question=question, context=context, history=history)
            except Exception:  # noqa: BLE001 - fall back to the templated answer
                return None
        return None

    async def handle(self, message: ChatMessage, trace_id: str | None = None) -> ChatResponse:
        trace_id = trace_id or uuid4().hex
        async with self._memory.lock(message.user_id, message.session_id):
            return await self._handle_locked(message, trace_id)

    async def _handle_locked(self, message: ChatMessage, trace_id: str) -> ChatResponse:
        started = time.perf_counter()
        memory = await self._memory.get(message.user_id, message.session_id)
        injection = self._guardrails_enabled and detect_prompt_injection(message.content)
        routed_agent: str | None = None
        if injection:
            intent = IntentResult(intent=Intent.UNKNOWN, confidence=0.0, reason="prompt_injection")
            answer = SAFE_DEFLECTION
            data, tools_used, need_human = {}, [], True
        else:
            intent = await self._resolve_intent(message.content)
            if intent.confidence < self._threshold or intent.intent == Intent.UNKNOWN:
                answer = "我还不能准确判断你的需求。请补充商品名称或 SKU；订单与售后能力将在下一阶段接入。"
                data, tools_used, need_human = {}, [], True
            elif intent.intent == Intent.GREETING:
                answer = (
                    "你好，我是 ShopAgent。可以帮你查商品、价格、参数和库存，也可以按场景推荐。"
                )
                data, tools_used, need_human = {}, [], False
            else:
                agent = self._agents.resolve(intent.intent)
                if agent:
                    routed_agent = agent.name
                    delegated_message = message.model_copy(
                        update={"context": {**message.context, "trace_id": trace_id}}
                    )
                    try:
                        result = await agent.execute(delegated_message, intent, memory)
                    except Exception:
                        logger.exception(
                            "domain agent unavailable: trace_id=%s agent=%s", trace_id, agent.name
                        )
                        answer = "当前自动服务暂时不可用，已为你转接人工客服，请稍后重试。"
                        data = {"error": "agent_unavailable", "retryable": True}
                        tools_used = []
                        need_human = True
                    else:
                        answer, data, tools_used = result.answer, result.data, result.tools_used
                        need_human = bool(
                            result.degraded or data.get("missing_fields") or data.get("error")
                        )
                else:
                    answer = "该业务能力尚未接入，请转人工客服处理。"
                    data, tools_used, need_human = (
                        {"recognized_intent": intent.intent.value},
                        [],
                        True,
                    )

        if self._redact_generated:
            answer = redact_sensitive_text(answer)

        memory.messages.extend(
            [
                MemoryMessage(role="user", content=message.content),
                MemoryMessage(role="assistant", content=answer),
            ]
        )
        memory.messages = memory.messages[-20:]
        if product_id := intent.entities.get("product_id"):
            memory.context["product_id"] = product_id
        elif match := re.search(r"SKU-\d+", answer, re.IGNORECASE):
            memory.context["product_id"] = match.group(0).upper()
        if order_id := intent.entities.get("order_id"):
            memory.context["order_id"] = order_id
        memory.context["last_intent"] = intent.intent.value
        memory.updated_at = datetime.now(UTC)
        await self._memory.save(memory)

        response = ChatResponse(
            trace_id=trace_id,
            intent=intent,
            answer=answer,
            data=data,
            tools_used=tools_used,
            routed_agent=routed_agent,
            need_human=need_human,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        await self._operations.record_interaction(
            InteractionRecord(
                trace_id=trace_id,
                user_id=message.user_id,
                session_id=message.session_id,
                question=redact_sensitive_text(message.content),
                answer=redact_sensitive_text(answer),
                intent=intent.intent,
                routed_agent=routed_agent,
                tools_used=tools_used,
                need_human=need_human,
                latency_ms=response.latency_ms,
            )
        )
        return response
