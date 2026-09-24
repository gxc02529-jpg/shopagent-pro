from __future__ import annotations

from shopagent.ports.orders import AfterSalesRepository, OrderRepository
from shopagent.tools.registry import ToolRegistry, ToolSpec


def register_order_tools(
    registry: ToolRegistry,
    orders: OrderRepository,
    after_sales: AfterSalesRepository,
) -> None:
    async def list_orders(user_id: str) -> dict:
        items = await orders.list_for_user(user_id)
        return {"orders": [item.model_dump(mode="json") for item in items], "count": len(items)}

    async def get_order(user_id: str, order_id: str) -> dict:
        order = await orders.get_order_for_user(user_id, order_id)
        return {"order": order.model_dump(mode="json") if order else None}

    async def get_logistics(user_id: str, order_id: str) -> dict:
        order = await orders.get_order_for_user(user_id, order_id)
        if not order:
            return {"order_id": order_id.upper(), "found": False}
        return {
            "order_id": order.id,
            "found": True,
            "tracking_number": order.tracking_number,
            "logistics_status": order.logistics_status,
        }

    async def get_refund_status(user_id: str, order_id: str) -> dict:
        order = await orders.get_order_for_user(user_id, order_id)
        return {
            "order_id": order_id.upper(),
            "found": order is not None,
            "refund_status": order.refund_status if order else None,
        }

    async def create_ticket(
        user_id: str, order_id: str, issue_type: str, description: str, idempotency_key: str = ""
    ) -> dict:
        order = await orders.get_order_for_user(user_id, order_id)
        if not order:
            return {"ticket": None, "error": "order_not_found_or_forbidden"}
        ticket = await after_sales.create(
            user_id=user_id,
            order_id=order_id,
            issue_type=issue_type,
            description=description,
            idempotency_key=idempotency_key,
        )
        return {"ticket": ticket.model_dump(mode="json")}

    async def get_ticket(user_id: str, ticket_id: str) -> dict:
        ticket = await after_sales.get_ticket_for_user(user_id, ticket_id)
        return {"ticket": ticket.model_dump(mode="json") if ticket else None}

    registry.register(
        ToolSpec(
            "order.list", "查询当前用户订单列表", "1.0.0", list_orders, frozenset({"order_agent"})
        )
    )
    registry.register(
        ToolSpec(
            "order.detail", "查询当前用户订单详情", "1.0.0", get_order, frozenset({"order_agent"})
        )
    )
    registry.register(
        ToolSpec(
            "order.logistics",
            "查询订单物流轨迹",
            "1.0.0",
            get_logistics,
            frozenset({"order_agent"}),
        )
    )
    registry.register(
        ToolSpec(
            "order.refund",
            "查询退款状态",
            "1.0.0",
            get_refund_status,
            frozenset({"order_agent", "after_sales_agent"}),
        )
    )
    registry.register(
        ToolSpec(
            "after_sales.create",
            "创建售后工单",
            "1.0.0",
            create_ticket,
            frozenset({"after_sales_agent"}),
        )
    )
    registry.register(
        ToolSpec(
            "after_sales.get",
            "查询售后工单进度",
            "1.0.0",
            get_ticket,
            frozenset({"after_sales_agent"}),
        )
    )
