from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime

from shopagent.domain.models import (
    AuditEvent,
    FeedbackRecord,
    InteractionRecord,
    KnowledgeCandidate,
)


class InMemoryOperationsStore:
    def __init__(self) -> None:
        self._interactions: dict[str, InteractionRecord] = {}
        self._feedback: dict[str, FeedbackRecord] = {}
        self._candidates: dict[str, KnowledgeCandidate] = {}
        self._audit: list[AuditEvent] = []
        self._tasks: dict[str, tuple[dict, str, datetime]] = {}
        self._lock = asyncio.Lock()

    async def record_interaction(self, record: InteractionRecord) -> None:
        async with self._lock:
            self._interactions.setdefault(record.trace_id, deepcopy(record))

    async def get_interaction(self, trace_id: str) -> InteractionRecord | None:
        async with self._lock:
            record = self._interactions.get(trace_id)
            return deepcopy(record) if record else None

    async def save_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        async with self._lock:
            if feedback.idempotency_key:
                existing = next(
                    (
                        value
                        for value in self._feedback.values()
                        if value.user_id == feedback.user_id
                        and value.idempotency_key == feedback.idempotency_key
                    ),
                    None,
                )
                if existing:
                    return deepcopy(existing)
            self._feedback[feedback.id] = deepcopy(feedback)
            return deepcopy(feedback)

    async def get_feedback_by_idempotency(self, user_id: str, key: str) -> FeedbackRecord | None:
        if not key:
            return None
        async with self._lock:
            item = next(
                (
                    value
                    for value in self._feedback.values()
                    if value.user_id == user_id and value.idempotency_key == key
                ),
                None,
            )
            return deepcopy(item) if item else None

    async def save_candidate(self, candidate: KnowledgeCandidate) -> None:
        async with self._lock:
            self._candidates[candidate.id] = deepcopy(candidate)

    async def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        async with self._lock:
            candidate = self._candidates.get(candidate_id)
            return deepcopy(candidate) if candidate else None

    async def update_candidate(self, candidate: KnowledgeCandidate) -> None:
        async with self._lock:
            self._candidates[candidate.id] = deepcopy(candidate)

    async def update_candidate_status(
        self,
        candidate_id: str,
        expected_status: str,
        *,
        new_status: str,
        reviewed_by: str | None = None,
    ) -> bool:
        async with self._lock:
            candidate = self._candidates.get(candidate_id)
            if candidate is None or candidate.status != expected_status:
                return False
            candidate.status = new_status
            if reviewed_by is not None:
                candidate.reviewed_by = reviewed_by
            return True

    async def save_task(self, task: dict, *, owner: str, expires_at: datetime) -> None:
        async with self._lock:
            if task["id"] in self._tasks:
                raise ValueError("task id already exists")
            self._tasks[task["id"]] = (deepcopy(task), owner, expires_at)

    async def get_task(self, task_id: str, *, owner: str) -> dict | None:
        async with self._lock:
            stored = self._tasks.get(task_id)
            if not stored:
                return None
            task, task_owner, expires_at = stored
            if expires_at <= datetime.now(UTC):
                self._tasks.pop(task_id, None)
                return None
            if task_owner != owner:
                return None
            return deepcopy(task)

    async def list_candidates(self, status: str | None = None) -> list[KnowledgeCandidate]:
        async with self._lock:
            values = self._candidates.values()
            return [deepcopy(item) for item in values if status is None or item.status == status]

    async def dashboard(self) -> dict:
        async with self._lock:
            interactions = list(self._interactions.values())
            feedback = list(self._feedback.values())
            total = len(interactions)
            automated = sum(not item.need_human for item in interactions)
            ratings = [item.rating for item in feedback]
            return {
                "total_interactions": total,
                "automated_resolutions": automated,
                "automation_rate": round(automated / total, 4) if total else 0,
                "human_handoffs": total - automated,
                "handoff_rate": round((total - automated) / total, 4) if total else 0,
                "feedback_count": len(feedback),
                "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "agent_distribution": dict(
                    Counter(item.routed_agent or "orchestrator" for item in interactions)
                ),
                "intent_distribution": dict(Counter(item.intent.value for item in interactions)),
                "pending_knowledge_candidates": sum(
                    item.status == "pending" for item in self._candidates.values()
                ),
                "average_latency_ms": round(
                    sum(item.latency_ms for item in interactions) / total, 2
                )
                if total
                else 0,
                "audit_event_count": len(self._audit),
                "a2a_task_count": sum(
                    expires_at > datetime.now(UTC) for _, _, expires_at in self._tasks.values()
                ),
            }

    async def record_audit(self, event: AuditEvent) -> None:
        async with self._lock:
            self._audit.append(deepcopy(event))

    async def list_audit(self, limit: int = 100) -> list[AuditEvent]:
        async with self._lock:
            return [deepcopy(item) for item in reversed(self._audit[-max(1, min(limit, 500)) :])]

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None
