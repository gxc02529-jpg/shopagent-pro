from __future__ import annotations

import re
from typing import Protocol

from shopagent.domain.models import KnowledgeDocument, KnowledgeHit
from shopagent.ports.knowledge import KnowledgeRepository


class RetrievalBackend(Protocol):
    """Pluggable knowledge-retrieval boundary (character / vector / hybrid)."""

    async def search(
        self, query: str, *, domain: str | None = None, limit: int = 3
    ) -> list[KnowledgeHit]: ...


def _cosine(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9
    return float(a @ b / denom)


class CharacterRecallBackend:
    """Zero-dependency 2-gram + keyword recall/rerank (default behavior)."""

    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repository = repository

    async def search(
        self, query: str, *, domain: str | None = None, limit: int = 3
    ) -> list[KnowledgeHit]:
        documents = await self._repository.list_documents(domain)
        ranked = sorted(
            ((self._score(query, document), document) for document in documents),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [
            self._to_hit(document, score) for score, document in ranked[:limit] if score >= 0.12
        ]

    @staticmethod
    def _terms(text: str) -> set[str]:
        lowered = text.lower()
        latin = set(re.findall(r"[a-z0-9]+", lowered))
        chinese = {lowered[index : index + 2] for index in range(max(0, len(lowered) - 1))}
        return latin | chinese

    def _score(self, query: str, document: KnowledgeDocument) -> float:
        query_terms = self._terms(query)
        if not query_terms:
            return 0.0
        title_terms = self._terms(document.title)
        content_terms = self._terms(document.content)
        keyword_text = " ".join(document.keywords).lower()
        keyword_hits = sum(1 for keyword in document.keywords if keyword.lower() in query.lower())
        title_overlap = len(query_terms & title_terms) / len(query_terms)
        content_overlap = len(query_terms & content_terms) / len(query_terms)
        exact_bonus = 0.25 if keyword_hits else 0
        reverse_bonus = 0.15 if any(term in keyword_text for term in query_terms) else 0
        return min(
            1.0,
            title_overlap * 0.35
            + content_overlap * 0.25
            + keyword_hits * 0.18
            + exact_bonus
            + reverse_bonus,
        )

    @staticmethod
    def _to_hit(document: KnowledgeDocument, score: float) -> KnowledgeHit:
        excerpt = (
            document.content if len(document.content) <= 180 else document.content[:177] + "..."
        )
        return KnowledgeHit(
            document_id=document.id,
            title=document.title,
            excerpt=excerpt,
            score=round(score, 4),
            source=document.source,
            version=document.version,
        )


class VectorRecallBackend:
    """Embedding-based recall (optional) with graceful fallback to character recall."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        fallback: CharacterRecallBackend | None = None,
        model_name: str = "BAAI/bge-m3",
    ) -> None:
        self._repository = repository
        self._fallback = fallback or CharacterRecallBackend(repository)
        self._model_name = model_name
        self._model = None

    async def search(
        self, query: str, *, domain: str | None = None, limit: int = 3
    ) -> list[KnowledgeHit]:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return await self._fallback.search(query, domain=domain, limit=limit)
        try:
            if self._model is None:
                self._model = SentenceTransformer(self._model_name)
            documents = await self._repository.list_documents(domain)
            if not documents:
                return []
            corpus = [f"{doc.title} {doc.content}" for doc in documents]
            query_emb = self._model.encode([query])[0]
            doc_embs = self._model.encode(corpus)
            sims = [_cosine(query_emb, emb) for emb in doc_embs]
            ranked = sorted(zip(documents, sims), key=lambda pair: pair[1], reverse=True)[:limit]
            return [
                self._fallback._to_hit(doc, max(0.0, min(1.0, float(sim)))) for doc, sim in ranked
            ]
        except Exception:  # noqa: BLE001 - degrade to character recall on model failure
            return await self._fallback.search(query, domain=domain, limit=limit)
