from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime

from shopagent.domain.models import (
    AuditEvent,
    FeedbackRecord,
    InteractionRecord,
    KnowledgeCandidate,
)


class MySQLOperationsStore:
    """SQLAlchemy-backed durable operations store (MySQL in production)."""

    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import (
                Boolean,
                Column,
                DateTime,
                Float,
                Integer,
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
        self._engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        metadata = MetaData()
        self._interactions = Table(
            "shopagent_interactions",
            metadata,
            Column("trace_id", String(64), primary_key=True),
            Column("user_id", String(128), nullable=False, index=True),
            Column("intent", String(64), nullable=False, index=True),
            Column("routed_agent", String(128)),
            Column("need_human", Boolean, nullable=False),
            Column("latency_ms", Float, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False, index=True),
            Column("payload", Text, nullable=False),
        )
        self._feedback = Table(
            "shopagent_feedback",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("trace_id", String(64), nullable=False, index=True),
            Column("user_id", String(128), nullable=False, index=True),
            Column("rating", Integer, nullable=False),
            Column("idempotency_key", String(128), nullable=True, index=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("payload", Text, nullable=False),
            UniqueConstraint("user_id", "idempotency_key", name="uq_feedback_user_idempotency"),
        )
        self._candidates = Table(
            "shopagent_knowledge_candidates",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("status", String(32), nullable=False, index=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("payload", Text, nullable=False),
        )
        self._audit = Table(
            "shopagent_audit_events",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("event_type", String(64), nullable=False, index=True),
            Column("actor_id", String(128), nullable=False, index=True),
            Column("entity_id", String(64), nullable=False, index=True),
            Column("trace_id", String(64), index=True),
            Column("created_at", DateTime(timezone=True), nullable=False, index=True),
            Column("payload", Text, nullable=False),
        )
        self._tasks = Table(
            "shopagent_a2a_tasks",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("owner", String(128), nullable=False, index=True),
            Column("status", String(64), nullable=False, index=True),
            Column("created_at", DateTime(timezone=True), nullable=False, index=True),
            Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
            Column("payload", Text, nullable=False),
        )
        metadata.create_all(self._engine)

    async def record_interaction(self, record: InteractionRecord) -> None:
        def insert_once() -> None:
            values = {
                "trace_id": record.trace_id,
                "user_id": record.user_id,
                "intent": record.intent.value,
                "routed_agent": record.routed_agent,
                "need_human": record.need_human,
                "latency_ms": record.latency_ms,
                "created_at": record.created_at,
                "payload": record.model_dump_json(),
            }
            try:
                with self._engine.begin() as connection:
                    connection.execute(self._sa.insert(self._interactions).values(**values))
            except self._sa.exc.IntegrityError:
                # Trace ids are immutable idempotency keys. Preserve the first
                # observed interaction rather than allowing a retry to rewrite it.
                return

        await asyncio.to_thread(insert_once)

    async def get_interaction(self, trace_id: str) -> InteractionRecord | None:
        payload = await asyncio.to_thread(
            self._payload_by_id, self._interactions, "trace_id", trace_id
        )
        return InteractionRecord.model_validate_json(payload) if payload else None

    async def save_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        def insert() -> FeedbackRecord:
            values = {
                "id": feedback.id,
                "trace_id": feedback.trace_id,
                "user_id": feedback.user_id,
                "rating": feedback.rating,
                "created_at": feedback.created_at,
                "idempotency_key": feedback.idempotency_key or None,
                "payload": feedback.model_dump_json(),
            }
            try:
                with self._engine.begin() as connection:
                    connection.execute(self._sa.insert(self._feedback).values(**values))
                return feedback
            except self._sa.exc.IntegrityError:
                if not feedback.idempotency_key:
                    raise
                with self._engine.connect() as connection:
                    payload = connection.execute(
                        self._sa.select(self._feedback.c.payload).where(
                            self._feedback.c.user_id == feedback.user_id,
                            self._feedback.c.idempotency_key == feedback.idempotency_key,
                        )
                    ).scalar_one_or_none()
                if payload:
                    return FeedbackRecord.model_validate_json(payload)
                raise

        return await asyncio.to_thread(insert)

    async def get_feedback_by_idempotency(self, user_id: str, key: str) -> FeedbackRecord | None:
        if not key:
            return None

        def query():
            with self._engine.connect() as connection:
                payload = connection.execute(
                    self._sa.select(self._feedback.c.payload).where(
                        self._feedback.c.user_id == user_id,
                        self._feedback.c.idempotency_key == key,
                    )
                ).scalar_one_or_none()
            return FeedbackRecord.model_validate_json(payload) if payload else None

        return await asyncio.to_thread(query)

    async def save_candidate(self, candidate: KnowledgeCandidate) -> None:
        await self.update_candidate(candidate)

    async def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        payload = await asyncio.to_thread(self._payload_by_id, self._candidates, "id", candidate_id)
        return KnowledgeCandidate.model_validate_json(payload) if payload else None

    async def update_candidate(self, candidate: KnowledgeCandidate) -> None:
        await asyncio.to_thread(
            self._replace,
            self._candidates,
            "id",
            candidate.id,
            {
                "id": candidate.id,
                "status": candidate.status,
                "created_at": candidate.created_at,
                "payload": candidate.model_dump_json(),
            },
        )

    async def update_candidate_status(
        self,
        candidate_id: str,
        expected_status: str,
        *,
        new_status: str,
        reviewed_by: str | None = None,
    ) -> bool:
        def update():
            with self._engine.begin() as connection:
                row = connection.execute(
                    self._sa.select(self._candidates.c.payload).where(
                        self._candidates.c.id == candidate_id,
                        self._candidates.c.status == expected_status,
                    )
                ).first()
                if row is None:
                    return False
                payload = json.loads(row[0])
                payload["status"] = new_status
                if reviewed_by is not None:
                    payload["reviewed_by"] = reviewed_by
                result = connection.execute(
                    self._sa.update(self._candidates)
                    .where(
                        self._candidates.c.id == candidate_id,
                        self._candidates.c.status == expected_status,
                    )
                    .values(status=new_status, payload=json.dumps(payload, ensure_ascii=False))
                )
                return result.rowcount == 1

        return await asyncio.to_thread(update)

    async def save_task(self, task: dict, *, owner: str, expires_at: datetime) -> None:
        def insert() -> None:
            with self._engine.begin() as connection:
                connection.execute(
                    self._sa.insert(self._tasks).values(
                        id=task["id"],
                        owner=owner,
                        status=(task.get("status") or {}).get("state", ""),
                        created_at=datetime.now(UTC),
                        expires_at=expires_at,
                        payload=json.dumps(task, ensure_ascii=False),
                    )
                )

        await asyncio.to_thread(insert)

    async def get_task(self, task_id: str, *, owner: str) -> dict | None:
        def query() -> str | None:
            now = datetime.now(UTC)
            with self._engine.begin() as connection:
                connection.execute(
                    self._sa.delete(self._tasks).where(self._tasks.c.expires_at <= now)
                )
                return connection.execute(
                    self._sa.select(self._tasks.c.payload).where(
                        self._tasks.c.id == task_id,
                        self._tasks.c.owner == owner,
                        self._tasks.c.expires_at > now,
                    )
                ).scalar_one_or_none()

        payload = await asyncio.to_thread(query)
        return json.loads(payload) if payload else None

    async def list_candidates(self, status: str | None = None) -> list[KnowledgeCandidate]:
        def query():
            statement = self._sa.select(self._candidates.c.payload)
            if status is not None:
                statement = statement.where(self._candidates.c.status == status)
            with self._engine.connect() as connection:
                return [
                    KnowledgeCandidate.model_validate_json(row[0])
                    for row in connection.execute(statement)
                ]

        return await asyncio.to_thread(query)

    async def dashboard(self) -> dict:
        def query():
            with self._engine.connect() as connection:
                interactions = list(
                    connection.execute(
                        self._sa.select(
                            self._interactions.c.intent,
                            self._interactions.c.routed_agent,
                            self._interactions.c.need_human,
                            self._interactions.c.latency_ms,
                        )
                    )
                )
                ratings = [
                    row[0] for row in connection.execute(self._sa.select(self._feedback.c.rating))
                ]
                pending = connection.execute(
                    self._sa.select(self._sa.func.count())
                    .select_from(self._candidates)
                    .where(self._candidates.c.status == "pending")
                ).scalar_one()
                audit_count = connection.execute(
                    self._sa.select(self._sa.func.count()).select_from(self._audit)
                ).scalar_one()
                task_count = connection.execute(
                    self._sa.select(self._sa.func.count())
                    .select_from(self._tasks)
                    .where(self._tasks.c.expires_at > datetime.now(UTC))
                ).scalar_one()
            total = len(interactions)
            automated = sum(not row.need_human for row in interactions)
            return {
                "total_interactions": total,
                "automated_resolutions": automated,
                "automation_rate": round(automated / total, 4) if total else 0,
                "human_handoffs": total - automated,
                "handoff_rate": round((total - automated) / total, 4) if total else 0,
                "feedback_count": len(ratings),
                "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "agent_distribution": dict(
                    Counter(row.routed_agent or "orchestrator" for row in interactions)
                ),
                "intent_distribution": dict(Counter(row.intent for row in interactions)),
                "pending_knowledge_candidates": pending,
                "average_latency_ms": round(sum(row.latency_ms for row in interactions) / total, 2)
                if total
                else 0,
                "audit_event_count": audit_count,
                "a2a_task_count": task_count,
            }

        return await asyncio.to_thread(query)

    async def record_audit(self, event: AuditEvent) -> None:
        await asyncio.to_thread(
            self._replace,
            self._audit,
            "id",
            event.id,
            {
                "id": event.id,
                "event_type": event.event_type,
                "actor_id": event.actor_id,
                "entity_id": event.entity_id,
                "trace_id": event.trace_id,
                "created_at": event.created_at,
                "payload": event.model_dump_json(),
            },
        )

    async def list_audit(self, limit: int = 100) -> list[AuditEvent]:
        def query():
            statement = (
                self._sa.select(self._audit.c.payload)
                .order_by(self._audit.c.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
            with self._engine.connect() as connection:
                return [
                    AuditEvent.model_validate_json(row[0]) for row in connection.execute(statement)
                ]

        return await asyncio.to_thread(query)

    async def health(self) -> bool:
        def check():
            with self._engine.connect() as connection:
                connection.execute(self._sa.text("SELECT 1"))
            return True

        return await asyncio.to_thread(check)

    async def close(self) -> None:
        await asyncio.to_thread(self._engine.dispose)

    def _replace(self, table, key_name: str, key_value: str, values: dict) -> None:
        with self._engine.begin() as connection:
            connection.execute(self._sa.delete(table).where(table.c[key_name] == key_value))
            connection.execute(self._sa.insert(table).values(**values))

    def _payload_by_id(self, table, key_name: str, key_value: str) -> str | None:
        with self._engine.connect() as connection:
            return connection.execute(
                self._sa.select(table.c.payload).where(table.c[key_name] == key_value)
            ).scalar_one_or_none()
