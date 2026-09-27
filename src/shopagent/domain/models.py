from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Channel(StrEnum):
    WEB = "web"
    APP = "app"
    WECHAT_MINI = "wechat_mini"
    DOUYIN = "douyin"
    WEWORK = "wework"


class Intent(StrEnum):
    PRODUCT_SEARCH = "product_search"
    PRODUCT_DETAIL = "product_detail"
    STOCK_QUERY = "stock_query"
    SALES_POLICY = "sales_policy"
    ORDER_QUERY = "order_query"
    AFTER_SALES = "after_sales"
    RECOMMENDATION = "recommendation"
    GREETING = "greeting"
    UNKNOWN = "unknown"


class KnowledgeCandidateStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    REJECTED = "rejected"


class KnowledgeTrustLevel(StrEnum):
    VERIFIED = "verified"
    INTERNAL = "internal"
    UNTRUSTED = "untrusted"


class LongTermMemoryKey(StrEnum):
    PREFERRED_CATEGORY = "preferred_category"
    BUDGET_RANGE = "budget_range"
    SIZE_PREFERENCE = "size_preference"
    COLOR_PREFERENCE = "color_preference"


class ChatContext(BaseModel):
    """Whitelisted caller context. Unknown keys fail at the API boundary."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    plan_task_id: str | None = Field(default=None, max_length=64)
    protocol: str | None = Field(default=None, max_length=32)
    tenant_id: str = Field(default="global", min_length=1, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    locale: str | None = Field(default=None, max_length=32)


class SessionContext(BaseModel):
    """Whitelisted durable session facts; never stores arbitrary model output."""

    model_config = ConfigDict(extra="forbid")

    product_id: str | None = Field(default=None, max_length=128)
    order_id: str | None = Field(default=None, max_length=128)
    last_intent: str | None = Field(default=None, max_length=64)
    last_intents: list[str] = Field(default_factory=list, max_length=5)
    # Kept as a typed integration flag rather than allowing arbitrary keys.
    verified: bool | None = None


class ChatMessage(BaseModel):
    channel: Channel = Channel.WEB
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context", mode="before")
    @classmethod
    def validate_context(cls, value: Any) -> dict[str, Any]:
        validated = ChatContext.model_validate(value or {})
        return validated.model_dump(exclude_none=True)


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    entities: dict[str, str] = Field(default_factory=dict)
    reason: str = ""


class Product(BaseModel):
    id: str
    name: str
    category: str
    price: float = Field(ge=0)
    stock: int = Field(ge=0)
    attributes: dict[str, str] = Field(default_factory=dict)
    description: str = ""


class Order(BaseModel):
    id: str
    user_id: str
    status: str
    amount: float = Field(ge=0)
    product_ids: list[str] = Field(default_factory=list)
    logistics_status: str = ""
    tracking_number: str = ""
    refund_status: str = "未申请"


class AfterSalesTicket(BaseModel):
    id: str
    user_id: str
    order_id: str
    issue_type: str
    description: str
    status: str = "已创建"
    idempotency_key: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeDocument(BaseModel):
    id: str
    domain: str
    title: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    version: str = "1.0"
    source: str = "internal"
    tenant_id: str = Field(default="global", min_length=1, max_length=128)
    departments: list[str] = Field(default_factory=list, max_length=50)
    channels: list[Channel] = Field(default_factory=list, max_length=10)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    trust_level: KnowledgeTrustLevel = KnowledgeTrustLevel.INTERNAL
    approved_by: str | None = Field(default=None, max_length=128)
    content_hash: str = Field(default="", max_length=64)
    evidence_only: bool = True

    @field_validator("valid_until")
    @classmethod
    def validate_validity_window(cls, value: datetime | None, info):
        start = info.data.get("valid_from")
        if value is not None and start is not None and value <= start:
            raise ValueError("valid_until must be later than valid_from")
        return value


class KnowledgeHit(BaseModel):
    document_id: str
    title: str
    excerpt: str
    score: float = Field(ge=0, le=1)
    source: str
    version: str
    trust_level: KnowledgeTrustLevel = KnowledgeTrustLevel.INTERNAL
    evidence_only: bool = True


class KnowledgeScope(BaseModel):
    """Authorization filters applied before knowledge is eligible for recall."""

    tenant_id: str = Field(default="global", min_length=1, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    channel: Channel | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryMessage(BaseModel):
    role: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionMemory(BaseModel):
    user_id: str
    session_id: str
    messages: list[MemoryMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("context", mode="before")
    @classmethod
    def validate_context(cls, value: Any) -> dict[str, Any]:
        validated = SessionContext.model_validate(value or {})
        return validated.model_dump(exclude_none=True)


class DelegatedMemory(BaseModel):
    """Minimal A2A state: identity, an opaque reference and task-relevant facts only."""

    model_config = ConfigDict(extra="forbid")

    memory_ref: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    product_id: str | None = Field(default=None, max_length=128)
    order_id: str | None = Field(default=None, max_length=128)

    def to_session_memory(self) -> SessionMemory:
        context = {
            key: value
            for key, value in {"product_id": self.product_id, "order_id": self.order_id}.items()
            if value
        }
        return SessionMemory(user_id=self.user_id, session_id=self.session_id, context=context)


class LongTermMemoryFact(BaseModel):
    """Governed personal-memory write contract; persistence is intentionally opt-in."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    key: LongTermMemoryKey
    value: str = Field(min_length=1, max_length=500)
    source_trace_id: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    consent_id: str = Field(min_length=1, max_length=128)
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        if value <= datetime.now(UTC):
            raise ValueError("long-term memory must have a future expiry")
        return value


class AgentResult(BaseModel):
    answer: str
    data: dict[str, Any] = Field(default_factory=dict)
    tools_used: list[str] = Field(default_factory=list)
    degraded: bool = False


class ChatResponse(BaseModel):
    trace_id: str
    intent: IntentResult
    answer: str
    data: dict[str, Any] = Field(default_factory=dict)
    tools_used: list[str] = Field(default_factory=list)
    routed_agent: str | None = None
    routing_decision: str = ""
    routing_threshold: float = Field(default=0.0, ge=0, le=1)
    need_human: bool = False
    latency_ms: float


class InteractionRecord(BaseModel):
    trace_id: str
    user_id: str
    session_id: str
    question: str
    answer: str
    intent: Intent
    routed_agent: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    need_human: bool = False
    latency_ms: float = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackRecord(BaseModel):
    id: str
    trace_id: str
    user_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = ""
    suggested_answer: str = ""
    idempotency_key: str = ""
    knowledge_candidate_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeCandidate(BaseModel):
    id: str
    trace_id: str
    question: str
    current_answer: str
    suggested_answer: str = ""
    reason: str
    domain: str
    status: KnowledgeCandidateStatus = KnowledgeCandidateStatus.PENDING
    reviewed_by: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEvent(BaseModel):
    id: str
    event_type: str
    actor_id: str
    entity_id: str
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
