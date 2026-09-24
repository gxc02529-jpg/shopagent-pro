from __future__ import annotations

from shopagent.domain.models import KnowledgeHit
from shopagent.ports.knowledge import KnowledgeRepository
from shopagent.ports.retrieval import CharacterRecallBackend, RetrievalBackend


class KnowledgeService:
    """Recall/rerank service behind a pluggable RetrievalBackend (vector-store ready)."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        backend: RetrievalBackend | None = None,
    ) -> None:
        self._repository = repository
        self._backend = backend or CharacterRecallBackend(repository)

    async def search(
        self, query: str, *, domain: str | None = None, limit: int = 3
    ) -> list[KnowledgeHit]:
        return await self._backend.search(query, domain=domain, limit=limit)
