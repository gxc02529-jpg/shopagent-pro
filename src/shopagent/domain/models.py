from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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
    ORDER_QUERY = "order_query"
    AFTER_SALES = "after_sales"
    RECOMMENDATION = "recommendation"
    GREETING = "greeting"
    UNKNOWN = "unknown"


class ChatMessage(BaseModel):
    channel: Channel = Channel.WEB
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


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


class KnowledgeHit(BaseModel):
    document_id: str
    title: str
    excerpt: str
    score: float = Field(ge=0, le=1)
    source: str
    version: str


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
    status: str = "pending"
    reviewed_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEvent(BaseModel):
    id: str
    event_type: str
    actor_id: str
    entity_id: str
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
