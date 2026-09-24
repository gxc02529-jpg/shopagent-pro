from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from shopagent.domain.models import AfterSalesTicket, Order


class MySQLCommerceAdapter:
    """Durable order read model and idempotent after-sales ticket repository."""

    def __init__(self, database_url: str, seed_orders: list[Order] | None = None) -> None:
        try:
            from sqlalchemy import (
                Column,
                DateTime,
                MetaData,
                String,
                Table,
                Text,
                UniqueConstraint,
                create_engine,
            )
        except ImportError as exc:
            raise RuntimeError('MySQL backend requires: pip install -e ".[enterprise]"') from exc
        self._sa = __import__("sqlalchemy")
        self._integrity_error = self._sa.exc.IntegrityError
        self._engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        metadata = MetaData()
        self._orders = Table(
            "shopagent_orders",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("user_id", String(128), nullable=False, index=True),
            Column("payload", Text, nullable=False),
        )
        self._tickets = Table(
            "shopagent_after_sales_tickets",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("user_id", String(128), nullable=False, index=True),
            Column("order_id", String(64), nullable=False, index=True),
            Column("idempotency_key", String(128), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False, index=True),
            Column("payload", Text, nullable=False),
            UniqueConstraint("user_id", "idempotency_key", name="uq_ticket_user_idempotency"),
        )
        metadata.create_all(self._engine)
        if seed_orders:
            self._seed_orders(seed_orders)

    def _seed_orders(self, orders: list[Order]) -> None:
        with self._engine.begin() as connection:
            count = connection.execute(
                self._sa.select(self._sa.func.count()).select_from(self._orders)
            ).scalar_one()
            if count == 0:
                connection.execute(
                    self._sa.insert(self._orders),
                    [
                        {"id": item.id, "user_id": item.user_id, "payload": item.model_dump_json()}
                        for item in orders
                    ],
                )

    async def list_for_user(self, user_id: str) -> list[Order]:
        def query():
            with self._engine.connect() as connection:
                rows = connection.execute(
                    self._sa.select(self._orders.c.payload).where(self._orders.c.user_id == user_id)
                )
                return [Order.model_validate_json(row[0]) for row in rows]

        return await asyncio.to_thread(query)

    async def create(
        self,
        *,
        user_id: str,
        order_id: str,
        issue_type: str,
        description: str,
        idempotency_key: str = "",
    ) -> AfterSalesTicket:
        def write():
            if idempotency_key:
                existing = self._ticket_by_key(user_id, idempotency_key)
                if existing:
                    return existing
            ticket = AfterSalesTicket(
                id=f"AS-{uuid4().hex[:10].upper()}",
                user_id=user_id,
                order_id=order_id.upper(),
                issue_type=issue_type,
                description=description,
                idempotency_key=idempotency_key,
            )
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        self._sa.insert(self._tickets).values(
                            id=ticket.id,
                            user_id=user_id,
                            order_id=ticket.order_id,
                            idempotency_key=idempotency_key or None,
                            created_at=datetime.now(UTC),
                            payload=ticket.model_dump_json(),
                        )
                    )
                return ticket
            except self._integrity_error:
                existing = self._ticket_by_key(user_id, idempotency_key)
                if existing:
                    return existing
                raise

        return await asyncio.to_thread(write)

    async def get_ticket_for_user(self, user_id: str, ticket_id: str) -> AfterSalesTicket | None:
        def query():
            with self._engine.connect() as connection:
                payload = connection.execute(
                    self._sa.select(self._tickets.c.payload).where(
                        self._tickets.c.id == ticket_id.upper(),
                        self._tickets.c.user_id == user_id,
                    )
                ).scalar_one_or_none()
                return AfterSalesTicket.model_validate_json(payload) if payload else None

        return await asyncio.to_thread(query)

    async def get_order_for_user(self, user_id: str, order_id: str) -> Order | None:
        def query():
            with self._engine.connect() as connection:
                payload = connection.execute(
                    self._sa.select(self._orders.c.payload).where(
                        self._orders.c.id == order_id.upper(), self._orders.c.user_id == user_id
                    )
                ).scalar_one_or_none()
                return Order.model_validate_json(payload) if payload else None

        return await asyncio.to_thread(query)

    def _ticket_by_key(self, user_id: str, key: str) -> AfterSalesTicket | None:
        if not key:
            return None
        with self._engine.connect() as connection:
            payload = connection.execute(
                self._sa.select(self._tickets.c.payload).where(
                    self._tickets.c.user_id == user_id,
                    self._tickets.c.idempotency_key == key,
                )
            ).scalar_one_or_none()
            return AfterSalesTicket.model_validate_json(payload) if payload else None

    async def health(self) -> bool:
        def check():
            with self._engine.connect() as connection:
                connection.execute(self._sa.text("SELECT 1"))
            return True

        return await asyncio.to_thread(check)

    async def close(self) -> None:
        await asyncio.to_thread(self._engine.dispose)
