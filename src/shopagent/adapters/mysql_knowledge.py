from __future__ import annotations

import asyncio

from shopagent.domain.models import KnowledgeDocument


class MySQLKnowledgeAdapter:
    def __init__(
        self, database_url: str, seed_documents: list[KnowledgeDocument] | None = None
    ) -> None:
        try:
            from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine
        except ImportError as exc:
            raise RuntimeError('MySQL backend requires: pip install -e ".[enterprise]"') from exc
        from datetime import UTC, datetime

        self._sa = __import__("sqlalchemy")
        self._engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        metadata = MetaData()
        self._table = Table(
            "shopagent_knowledge_documents",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("domain", String(64), nullable=False, index=True),
            Column("version", String(64), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("payload", Text, nullable=False),
        )
        metadata.create_all(self._engine)
        if seed_documents:
            with self._engine.begin() as connection:
                count = connection.execute(
                    self._sa.select(self._sa.func.count()).select_from(self._table)
                ).scalar_one()
                if count == 0:
                    connection.execute(
                        self._sa.insert(self._table),
                        [
                            {
                                "id": item.id,
                                "domain": item.domain,
                                "version": item.version,
                                "updated_at": datetime.now(UTC),
                                "payload": item.model_dump_json(),
                            }
                            for item in seed_documents
                        ],
                    )

    async def list_documents(self, domain: str | None = None) -> list[KnowledgeDocument]:
        def query():
            statement = self._sa.select(self._table.c.payload)
            if domain:
                statement = statement.where(self._table.c.domain == domain)
            with self._engine.connect() as connection:
                return [
                    KnowledgeDocument.model_validate_json(row[0])
                    for row in connection.execute(statement)
                ]

        return await asyncio.to_thread(query)

    async def upsert(self, document: KnowledgeDocument) -> None:
        from datetime import UTC, datetime

        def write():
            with self._engine.begin() as connection:
                connection.execute(
                    self._sa.delete(self._table).where(self._table.c.id == document.id)
                )
                connection.execute(
                    self._sa.insert(self._table).values(
                        id=document.id,
                        domain=document.domain,
                        version=document.version,
                        updated_at=datetime.now(UTC),
                        payload=document.model_dump_json(),
                    )
                )

        await asyncio.to_thread(write)

    async def health(self) -> bool:
        def check():
            with self._engine.connect() as connection:
                connection.execute(self._sa.text("SELECT 1"))
            return True

        return await asyncio.to_thread(check)

    async def close(self) -> None:
        await asyncio.to_thread(self._engine.dispose)
