from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shopagent.domain.models import LongTermMemoryFact, LongTermMemoryKey
from shopagent.security.redaction import redact_sensitive_text


class MemoryWriteRejected(ValueError):
    pass


def build_long_term_memory_fact(
    *,
    tenant_id: str,
    user_id: str,
    key: LongTermMemoryKey,
    value: str,
    source_trace_id: str,
    confidence: float,
    consent_id: str,
    ttl_days: int = 90,
) -> LongTermMemoryFact:
    """Enforce allowlisted keys, consent, provenance, confidence and expiry."""

    if not 1 <= ttl_days <= 365:
        raise MemoryWriteRejected("memory ttl_days must be between 1 and 365")
    if confidence < 0.80:
        raise MemoryWriteRejected("memory confidence is below the write threshold")
    if redact_sensitive_text(value) != value:
        raise MemoryWriteRejected("sensitive information cannot be stored as preference memory")
    return LongTermMemoryFact(
        tenant_id=tenant_id,
        user_id=user_id,
        key=key,
        value=value,
        source_trace_id=source_trace_id,
        confidence=confidence,
        consent_id=consent_id,
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )
