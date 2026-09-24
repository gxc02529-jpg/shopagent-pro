from __future__ import annotations

from uuid import uuid4

from shopagent.domain.models import AfterSalesTicket, Order

DEFAULT_ORDERS = [
    Order(
        id="ORD-20260001",
        user_id="demo-user",
        status="运输中",
        amount=899.0,
        product_ids=["SKU-1001"],
        logistics_status="已到达上海分拨中心，预计明日送达",
        tracking_number="SF1234567890",
    ),
    Order(
        id="ORD-20260002",
        user_id="demo-user",
        status="已完成",
        amount=269.0,
        product_ids=["SKU-2001"],
        logistics_status="已签收",
        tracking_number="YT9876543210",
        refund_status="退款处理中",
    ),
]


class MockOrderAdapter:
    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = {item.id: item for item in (orders or DEFAULT_ORDERS)}

    async def list_for_user(self, user_id: str) -> list[Order]:
        return [item for item in self._orders.values() if item.user_id == user_id]

    async def get_order_for_user(self, user_id: str, order_id: str) -> Order | None:
        order = self._orders.get(order_id.upper())
        return order if order and order.user_id == user_id else None


class MockAfterSalesAdapter:
    def __init__(self) -> None:
        self._tickets: dict[str, AfterSalesTicket] = {}
        self._idempotency: dict[tuple[str, str], str] = {}

    async def create(
        self,
        *,
        user_id: str,
        order_id: str,
        issue_type: str,
        description: str,
        idempotency_key: str = "",
    ) -> AfterSalesTicket:
        scope = (user_id, idempotency_key)
        if idempotency_key and scope in self._idempotency:
            return self._tickets[self._idempotency[scope]]
        ticket = AfterSalesTicket(
            id=f"AS-{uuid4().hex[:10].upper()}",
            user_id=user_id,
            order_id=order_id.upper(),
            issue_type=issue_type,
            description=description,
            idempotency_key=idempotency_key,
        )
        self._tickets[ticket.id] = ticket
        if idempotency_key:
            self._idempotency[scope] = ticket.id
        return ticket

    async def get_ticket_for_user(self, user_id: str, ticket_id: str) -> AfterSalesTicket | None:
        ticket = self._tickets.get(ticket_id.upper())
        return ticket if ticket and ticket.user_id == user_id else None

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None
