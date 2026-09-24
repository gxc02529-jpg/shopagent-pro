from __future__ import annotations

from uuid import uuid4

from shopagent.domain.models import (
    AuditEvent,
    FeedbackRecord,
    KnowledgeCandidate,
    KnowledgeDocument,
)
from shopagent.ports.knowledge import KnowledgeRepository
from shopagent.ports.operations import OperationsStore
from shopagent.security.redaction import redact_sensitive_text


class FeedbackLoopService:
    def __init__(self, store: OperationsStore, knowledge: KnowledgeRepository) -> None:
        self._store = store
        self._knowledge = knowledge

    async def submit(
        self,
        *,
        trace_id: str,
        user_id: str,
        rating: int,
        comment: str = "",
        suggested_answer: str = "",
        idempotency_key: str = "",
    ) -> tuple[FeedbackRecord, KnowledgeCandidate | None]:
        existing = await self._store.get_feedback_by_idempotency(user_id, idempotency_key)
        if existing:
            candidate = (
                await self._store.get_candidate(existing.knowledge_candidate_id)
                if existing.knowledge_candidate_id
                else None
            )
            return existing, candidate
        interaction = await self._store.get_interaction(trace_id)
        if not interaction:
            raise KeyError("interaction not found")
        if interaction.user_id != user_id:
            raise PermissionError("feedback user does not own this interaction")
        candidate_id = f"KC-{uuid4().hex[:10].upper()}" if rating <= 2 else None
        feedback = FeedbackRecord(
            id=f"FB-{uuid4().hex[:10].upper()}",
            trace_id=trace_id,
            user_id=user_id,
            rating=rating,
            comment=redact_sensitive_text(comment),
            suggested_answer=redact_sensitive_text(suggested_answer),
            idempotency_key=idempotency_key,
            knowledge_candidate_id=candidate_id,
        )
        saved_feedback = await self._store.save_feedback(feedback)
        if saved_feedback.id != feedback.id:
            candidate = (
                await self._store.get_candidate(saved_feedback.knowledge_candidate_id)
                if saved_feedback.knowledge_candidate_id
                else None
            )
            return saved_feedback, candidate
        feedback = saved_feedback
        candidate = None
        if rating <= 2:
            candidate = KnowledgeCandidate(
                id=candidate_id,
                trace_id=trace_id,
                question=interaction.question,
                current_answer=interaction.answer,
                suggested_answer=redact_sensitive_text(suggested_answer),
                reason=redact_sensitive_text(comment) or f"user rating: {rating}",
                domain=self._domain(interaction.intent.value),
            )
            await self._store.save_candidate(candidate)
        await self._store.record_audit(
            AuditEvent(
                id=f"AUD-{uuid4().hex[:12].upper()}",
                event_type="feedback.submitted",
                actor_id=user_id,
                entity_id=feedback.id,
                trace_id=trace_id,
                payload={"rating": rating, "candidate_id": candidate_id},
            )
        )
        return feedback, candidate

    async def review(
        self, candidate_id: str, *, approved: bool, reviewer: str
    ) -> KnowledgeCandidate:
        claimed_status = "publishing" if approved else "rejected"
        updated = await self._store.update_candidate_status(
            candidate_id, "pending", new_status=claimed_status, reviewed_by=reviewer
        )
        if not updated and approved:
            updated = await self._store.update_candidate_status(
                candidate_id,
                "publish_failed",
                new_status="publishing",
                reviewed_by=reviewer,
            )
        if not updated:
            existing = await self._store.get_candidate(candidate_id)
            if existing is None:
                raise KeyError("candidate not found")
            raise ValueError("candidate already reviewed")
        candidate = await self._store.get_candidate(candidate_id)
        if approved:
            answer = candidate.suggested_answer.strip() or candidate.current_answer
            try:
                await self._knowledge.upsert(
                    KnowledgeDocument(
                        id=f"KB-FEEDBACK-{candidate.id[3:]}",
                        domain=candidate.domain,
                        title=f"反馈改进：{candidate.question[:30]}",
                        content=answer,
                        keywords=[candidate.question],
                        version="feedback-v1",
                        source="客服反馈审核",
                    )
                )
            except Exception:
                await self._store.update_candidate_status(
                    candidate_id,
                    "publishing",
                    new_status="publish_failed",
                    reviewed_by=reviewer,
                )
                await self._store.record_audit(
                    AuditEvent(
                        id=f"AUD-{uuid4().hex[:12].upper()}",
                        event_type="knowledge.publish_failed",
                        actor_id=reviewer,
                        entity_id=candidate.id,
                        trace_id=candidate.trace_id,
                    )
                )
                raise
            finalized = await self._store.update_candidate_status(
                candidate_id,
                "publishing",
                new_status="published",
                reviewed_by=reviewer,
            )
            if not finalized:
                await self._store.update_candidate_status(
                    candidate_id,
                    "publishing",
                    new_status="publish_failed",
                    reviewed_by=reviewer,
                )
                raise RuntimeError("candidate publication state changed unexpectedly")
            candidate = await self._store.get_candidate(candidate_id)
        await self._store.record_audit(
            AuditEvent(
                id=f"AUD-{uuid4().hex[:12].upper()}",
                event_type="knowledge.published" if approved else "knowledge.rejected",
                actor_id=reviewer,
                entity_id=candidate.id,
                trace_id=candidate.trace_id,
                payload={"status": candidate.status},
            )
        )
        return candidate

    @staticmethod
    def _domain(intent: str) -> str:
        if intent == "after_sales":
            return "after_sales"
        if intent == "order_query":
            return "order"
        return "product"
