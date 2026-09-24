from typing import Protocol

from shopagent.domain.models import KnowledgeDocument


class KnowledgeRepository(Protocol):
    async def list_documents(self, domain: str | None = None) -> list[KnowledgeDocument]: ...

    async def upsert(self, document: KnowledgeDocument) -> None: ...
