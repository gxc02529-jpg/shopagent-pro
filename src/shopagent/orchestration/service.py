from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from shopagent.agents.base import AgentRegistry
from shopagent.agents.intent import IntentAgent
from shopagent.domain.models import (
    AgentResult,
    ChatMessage,
    ChatResponse,
    Intent,
    IntentResult,
    InteractionRecord,
    MemoryMessage,
)
from shopagent.orchestration.planner import ExecutionPlan, PlannedTask, QueryPlanner
from shopagent.ports.llm import LLMProvider
from shopagent.ports.memory import MemoryStore
from shopagent.ports.operations import OperationsStore
from shopagent.security.guardrails import SAFE_DEFLECTION, detect_prompt_injection
from shopagent.security.redaction import redact_sensitive_text

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _TaskOutcome:
    task: PlannedTask
    state: str
    answer: str
    data: dict
    tools_used: list[str]
    agent_name: str | None
    need_human: bool = False


class ShopAgentOrchestrator:
    def __init__(
        self,
        *,
        intent_agent: IntentAgent,
        agents: AgentRegistry,
        memory_store: MemoryStore,
        operations: OperationsStore,
        low_confidence_threshold: float = 0.80,
        session_max_messages: int = 20,
        max_plan_tasks: int = 5,
        max_parallel_tasks: int = 4,
        llm: LLMProvider | None = None,
        guardrails_enabled: bool = False,
        redact_generated: bool = False,
    ) -> None:
        self._intent = intent_agent
        self._agents = agents
        self._memory = memory_store
        self._operations = operations
        self._threshold = low_confidence_threshold
        self._session_max_messages = session_max_messages
        self._planner = QueryPlanner(max_tasks=max_plan_tasks)
        self._parallel_limit = asyncio.Semaphore(max_parallel_tasks)
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
        routing_decision = ""
        resolved_intents: list[IntentResult] = []
        if injection:
            intent = IntentResult(intent=Intent.UNKNOWN, confidence=0.0, reason="prompt_injection")
            resolved_intents = [intent]
            answer = SAFE_DEFLECTION
            data, tools_used, need_human = {}, [], True
            routing_decision = "guardrail_handoff"
        else:
            plan = await self._planner.build(message.content, self._resolve_intent)
            resolved_intents = [task.intent for task in plan.tasks]
            intent = resolved_intents[0]
            if plan.is_multi:
                (
                    answer,
                    data,
                    tools_used,
                    need_human,
                    routed_agent,
                    routing_decision,
                ) = await self._execute_plan(plan, message, memory, trace_id)
            else:
                if intent.confidence < self._threshold or intent.intent == Intent.UNKNOWN:
                    answer = "我还不能准确判断你的需求，请补充商品、订单号或具体办理事项。"
                    data, tools_used, need_human = {}, [], True
                    routing_decision = "low_confidence_handoff"
                elif intent.intent == Intent.GREETING:
                    answer = (
                        "你好，我是 ShopAgent。可以帮你查商品、价格、参数和库存，也可以按场景推荐。"
                    )
                    data, tools_used, need_human = {}, [], False
                    routing_decision = "orchestrator_direct"
                else:
                    agent = self._agents.resolve(intent.intent)
                    if agent:
                        routed_agent = agent.name
                        routing_decision = "agent_delegated"
                        delegated_message = message.model_copy(
                            update={"context": {**message.context, "trace_id": trace_id}}
                        )
                        try:
                            result = await agent.execute(delegated_message, intent, memory)
                        except Exception:
                            logger.exception(
                                "domain agent unavailable: trace_id=%s agent=%s",
                                trace_id,
                                agent.name,
                            )
                            answer = "当前自动服务暂时不可用，已为你转接人工客服，请稍后重试。"
                            data = {"error": "agent_unavailable", "retryable": True}
                            tools_used = []
                            need_human = True
                            routing_decision = "agent_error_handoff"
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
                        routing_decision = "capability_handoff"

        if self._redact_generated:
            answer = redact_sensitive_text(answer)

        memory.messages.extend(
            [
                MemoryMessage(role="user", content=message.content),
                MemoryMessage(role="assistant", content=answer),
            ]
        )
        memory.messages = memory.messages[-self._session_max_messages :]
        has_product_entity = False
        for resolved_intent in resolved_intents:
            if product_id := resolved_intent.entities.get("product_id"):
                memory.context["product_id"] = product_id
                has_product_entity = True
            if order_id := resolved_intent.entities.get("order_id"):
                memory.context["order_id"] = order_id
        if not has_product_entity and (match := re.search(r"SKU-\d+", answer, re.IGNORECASE)):
            memory.context["product_id"] = match.group(0).upper()
        memory.context["last_intent"] = intent.intent.value
        memory.context["last_intents"] = [item.intent.value for item in resolved_intents]
        memory.updated_at = datetime.now(UTC)
        await self._memory.save(memory)

        response = ChatResponse(
            trace_id=trace_id,
            intent=intent,
            answer=answer,
            data=data,
            tools_used=tools_used,
            routed_agent=routed_agent,
            routing_decision=routing_decision,
            routing_threshold=self._threshold,
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

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        message: ChatMessage,
        memory,
        trace_id: str,
    ) -> tuple[str, dict, list[str], bool, str, str]:
        outcomes: dict[str, _TaskOutcome] = {}
        if plan.is_parallel:
            completed = await asyncio.gather(
                *(
                    self._execute_planned_task(task, message, memory, trace_id)
                    for task in plan.tasks
                )
            )
            outcomes.update({item.task.id: item for item in completed})
            decision = "multi_agent_parallel"
        else:
            for task in plan.tasks:
                dependencies = [outcomes[item] for item in task.depends_on if item in outcomes]
                if dependencies and any(item.need_human for item in dependencies):
                    outcomes[task.id] = _TaskOutcome(
                        task=task,
                        state="blocked",
                        answer="前置任务未完成，后续操作未执行，已转人工继续处理。",
                        data={"blocked_by": list(task.depends_on)},
                        tools_used=[],
                        agent_name=None,
                        need_human=True,
                    )
                    continue
                if task.condition:
                    condition_result = self._condition_matches(task.condition, dependencies)
                    if condition_result is False:
                        outcomes[task.id] = _TaskOutcome(
                            task=task,
                            state="skipped",
                            answer=f"条件“{task.condition}”不成立，未执行后续操作。",
                            data={"condition": task.condition, "matched": False},
                            tools_used=[],
                            agent_name=None,
                        )
                        continue
                    if condition_result is None:
                        outcomes[task.id] = _TaskOutcome(
                            task=task,
                            state="input_required",
                            answer=f"暂时无法可靠判断条件“{task.condition}”，需要补充信息或人工确认。",
                            data={"condition": task.condition, "matched": None},
                            tools_used=[],
                            agent_name=None,
                            need_human=True,
                        )
                        continue
                outcomes[task.id] = await self._execute_planned_task(
                    task, message, memory, trace_id
                )
            decision = "complex_task_sequential"

        ordered = [outcomes[task.id] for task in plan.tasks]
        answer = "\n\n".join(
            f"{index}. {item.answer}" for index, item in enumerate(ordered, start=1)
        )
        tools_used = list(dict.fromkeys(tool for item in ordered for tool in item.tools_used))
        need_human = any(item.need_human for item in ordered)
        data = {
            "plan_type": "parallel" if plan.is_parallel else "sequential",
            "task_count": len(ordered),
            "tasks": [
                {
                    "id": item.task.id,
                    "content": item.task.content,
                    "intent": item.task.intent.model_dump(mode="json"),
                    "depends_on": list(item.task.depends_on),
                    "condition": item.task.condition,
                    "state": item.state,
                    "routed_agent": item.agent_name,
                    "tools_used": item.tools_used,
                    "need_human": item.need_human,
                    "result": item.data,
                }
                for item in ordered
            ],
        }
        return answer, data, tools_used, need_human, "multi_agent", decision

    async def _execute_planned_task(
        self,
        task: PlannedTask,
        message: ChatMessage,
        memory,
        trace_id: str,
    ) -> _TaskOutcome:
        intent = task.intent
        if intent.confidence < self._threshold or intent.intent == Intent.UNKNOWN:
            return _TaskOutcome(
                task=task,
                state="input_required",
                answer=f"无法可靠识别子问题“{task.content}”，需要补充信息或转人工。",
                data={"confidence": intent.confidence},
                tools_used=[],
                agent_name=None,
                need_human=True,
            )
        if intent.intent == Intent.GREETING:
            return _TaskOutcome(
                task=task,
                state="completed",
                answer="你好，我可以继续处理商品、订单、推荐和售后问题。",
                data={},
                tools_used=[],
                agent_name=None,
            )
        agent = self._agents.resolve(intent.intent)
        if agent is None:
            return _TaskOutcome(
                task=task,
                state="rejected",
                answer=f"子问题“{task.content}”对应的业务能力尚未接入，已转人工。",
                data={"recognized_intent": intent.intent.value},
                tools_used=[],
                agent_name=None,
                need_human=True,
            )

        task_memory = memory.model_copy(deep=True)
        task_memory.context.update(intent.entities)
        delegated_message = message.model_copy(
            update={
                "content": task.content,
                "context": {
                    **message.context,
                    "trace_id": trace_id,
                    "plan_task_id": task.id,
                },
            }
        )
        try:
            async with self._parallel_limit:
                result: AgentResult = await agent.execute(delegated_message, intent, task_memory)
        except Exception:
            logger.exception(
                "planned domain task failed: trace_id=%s task=%s agent=%s",
                trace_id,
                task.id,
                agent.name,
            )
            return _TaskOutcome(
                task=task,
                state="failed",
                answer=f"子问题“{task.content}”暂时处理失败，其他问题不受影响，已转人工。",
                data={"error": "agent_unavailable", "retryable": True},
                tools_used=[],
                agent_name=agent.name,
                need_human=True,
            )
        need_human = bool(
            result.degraded or result.data.get("missing_fields") or result.data.get("error")
        )
        state = "input_required" if result.data.get("missing_fields") else "completed"
        if result.degraded or result.data.get("error"):
            state = "partial"
        return _TaskOutcome(
            task=task,
            state=state,
            answer=result.answer,
            data=result.data,
            tools_used=result.tools_used,
            agent_name=agent.name,
            need_human=need_human,
        )

    @staticmethod
    def _condition_matches(condition: str, dependencies: list[_TaskOutcome]) -> bool | None:
        serialized = json.dumps(
            [item.data for item in dependencies], ensure_ascii=False, default=str
        )
        normalized = condition.replace("还", "").replace("尚", "")
        if any(marker in normalized for marker in ("没发货", "未发货")):
            if any(marker in serialized for marker in ("待发货", "未发货", "待出库")):
                return True
            if any(
                marker in serialized for marker in ("已发货", "运输中", "分拨", "派送", "已签收")
            ):
                return False
            return None
        if "有货" in normalized:
            for item in dependencies:
                stock = item.data.get("stock")
                if isinstance(stock, int):
                    return stock > 0
            return None
        return None
