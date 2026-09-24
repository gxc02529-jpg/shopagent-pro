from __future__ import annotations

import hashlib
import re

from shopagent.domain.models import AgentResult, ChatMessage, IntentResult, SessionMemory
from shopagent.ports.tools import ToolClient


class AfterSalesAgent:
    name = "after_sales_agent"
    _order_pattern = re.compile(r"ORD-\d+", re.IGNORECASE)
    _ticket_pattern = re.compile(r"AS-[A-Z0-9]+", re.IGNORECASE)

    def __init__(self, tools: ToolClient) -> None:
        self._tools = tools

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        match = self._order_pattern.search(message.content)
        order_id = match.group(0).upper() if match else str(memory.context.get("order_id", ""))
        if ticket_match := self._ticket_pattern.search(message.content):
            ticket_id = ticket_match.group(0).upper()
            data = await self._tools.call(
                "after_sales.get",
                agent_name=self.name,
                user_id=message.user_id,
                ticket_id=ticket_id,
            )
            ticket = data["ticket"]
            answer = (
                f"售后工单 {ticket_id} 当前状态：{ticket['status']}，类型：{ticket['issue_type']}，"
                f"关联订单：{ticket['order_id']}。"
                if ticket
                else "没有找到该工单，或该工单不属于当前用户。"
            )
            return AgentResult(answer=answer, data=data, tools_used=["after_sales.get"])
        policy_markers = ("规则", "政策", "条件", "多久", "怎么", "是否", "运费险", "发票")
        action_markers = ("我要", "帮我", "申请", "创建", "办理", "投诉")
        is_policy_question = any(word in message.content for word in policy_markers)
        is_action_request = any(word in message.content for word in action_markers)
        if is_policy_question and not (order_id and is_action_request):
            data = await self._tools.call(
                "knowledge.search",
                agent_name=self.name,
                query=message.content,
                domain="after_sales",
                limit=3,
            )
            hits = data["hits"]
            if not hits:
                return AgentResult(
                    answer="知识库中暂未找到可靠答案，我会为你转人工客服。",
                    data=data,
                    tools_used=["knowledge.search"],
                    degraded=True,
                )
            evidence = hits[0]
            answer = (
                f"{evidence['excerpt']}\n"
                f"依据：{evidence['title']}（{evidence['source']}，版本 {evidence['version']}，"
                f"知识编号 {evidence['document_id']}）"
            )
            return AgentResult(answer=answer, data=data, tools_used=["knowledge.search"])
        if not order_id:
            return AgentResult(
                answer="办理售后需要订单号，请提供 ORD- 开头的订单号和问题描述。",
                data={"missing_fields": ["order_id"]},
            )
        issue_type = next(
            (name for name in ("退货", "换货", "退款", "发票", "投诉") if name in message.content),
            "售后咨询",
        )
        idempotency_key = str(
            message.context.get("idempotency_key") or message.context.get("message_id") or ""
        )
        if not idempotency_key:
            raw_key = (
                f"{message.user_id}|{message.session_id}|{order_id}|{issue_type}|{message.content}"
            )
            idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        data = await self._tools.call(
            "after_sales.create",
            agent_name=self.name,
            user_id=message.user_id,
            order_id=order_id,
            issue_type=issue_type,
            description=message.content,
            idempotency_key=idempotency_key,
        )
        ticket = data.get("ticket")
        if not ticket:
            return AgentResult(
                answer="没有找到该订单，或该订单不属于当前用户，暂时无法创建售后工单。",
                data=data,
                tools_used=["after_sales.create"],
            )
        return AgentResult(
            answer=f"已创建{issue_type}工单 {ticket['id']}，关联订单 {order_id}，当前状态：{ticket['status']}。",
            data=data,
            tools_used=["after_sales.create"],
        )
