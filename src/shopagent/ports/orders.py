from typing import Protocol

from shopagent.domain.models import AfterSalesTicket, Order


class OrderRepository(Protocol):
    async def list_for_user(self, user_id: str) -> list[Order]: ...

    async def get_order_for_user(self, user_id: str, order_id: str) -> Order | None: ...


class AfterSalesRepository(Protocol):
    async def create(
        self,
        *,
        user_id: str,
        order_id: str,
        issue_type: str,
        description: str,
        idempotency_key: str = "",
    ) -> AfterSalesTicket: ...

    async def get_ticket_for_user(
        self, user_id: str, ticket_id: str
    ) -> AfterSalesTicket | None: ...
