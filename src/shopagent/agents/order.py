from __future__ import annotations

import re

from shopagent.agents.knowledge_support import curated_feedback_answer
from shopagent.domain.models import AgentResult, ChatMessage, IntentResult, SessionMemory
from shopagent.ports.tools import ToolClient


class OrderAgent:
    name = "order_agent"
    _order_pattern = re.compile(r"ORD-\d+", re.IGNORECASE)

    def __init__(self, tools: ToolClient) -> None:
        self._tools = tools

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult:
        match = self._order_pattern.search(message.content)
        order_id = match.group(0).upper() if match else str(memory.context.get("order_id", ""))
        is_refund = any(word in message.content for word in ("退款", "退钱"))
        is_logistics = any(
            word in message.content for word in ("物流", "快递", "到哪", "送达", "发货")
        )

        if order_id and is_refund:
            data = await self._tools.call(
                "order.refund", agent_name=self.name, user_id=message.user_id, order_id=order_id
            )
            answer = (
                f"订单 {order_id} 的退款状态：{data['refund_status']}。"
                if data["found"]
                else "没有找到该订单，或该订单不属于当前用户。"
            )
            return AgentResult(answer=answer, data=data, tools_used=["order.refund"])

        if order_id and is_logistics:
            data = await self._tools.call(
                "order.logistics", agent_name=self.name, user_id=message.user_id, order_id=order_id
            )
            answer = (
                f"订单 {order_id}，运单号 {data['tracking_number']}：{data['logistics_status']}。"
                if data["found"]
                else "没有找到该订单，或该订单不属于当前用户。"
            )
            return AgentResult(answer=answer, data=data, tools_used=["order.logistics"])

        if order_id:
            data = await self._tools.call(
                "order.detail", agent_name=self.name, user_id=message.user_id, order_id=order_id
            )
            order = data["order"]
            answer = (
                f"订单 {order['id']} 当前状态为{order['status']}，金额 ¥{order['amount']:.2f}。"
                if order
                else "没有找到该订单，或该订单不属于当前用户。"
            )
            return AgentResult(answer=answer, data=data, tools_used=["order.detail"])

        if curated := await curated_feedback_answer(
            self._tools,
            agent_name=self.name,
            query=message.content,
            domain="order",
        ):
            return curated

        data = await self._tools.call("order.list", agent_name=self.name, user_id=message.user_id)
        if not data["orders"]:
            answer = "当前账号下没有可查询的订单。演示账号可使用 user_id：demo-user。"
        else:
            lines = [
                f"- {item['id']}：{item['status']}，¥{item['amount']:.2f}"
                for item in data["orders"]
            ]
            answer = "你的订单：\n" + "\n".join(lines) + "\n回复订单号可继续查询详情或物流。"
        return AgentResult(answer=answer, data=data, tools_used=["order.list"])
