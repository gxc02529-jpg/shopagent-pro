from __future__ import annotations

import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from shopagent.container import Container, build_container
from shopagent.domain.models import ChatMessage
from shopagent.protocols.a2a import A2AServerAdapter
from shopagent.protocols.mcp import MCPServerAdapter
from shopagent.security.tokens import Principal, TokenError, verify_token
from shopagent.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("shopagent")


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)
    suggested_answer: str = Field(default="", max_length=4000)


class CandidateReviewRequest(BaseModel):
    approved: bool


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_startup()
    configured_log_level = getattr(logging, settings.log_level.upper())
    logger.setLevel(configured_log_level)
    logging.getLogger("httpx").setLevel(max(configured_log_level, logging.WARNING))
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for component in (
            container.memory,
            container.operations,
            container.knowledge_repository,
            container.commerce,
            *container.resources,
        ):
            try:
                await component.close()
            except Exception:
                logger.exception("failed to close resource: %s", type(component).__name__)

    app = FastAPI(title="ShopAgent Pro API", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    mcp = MCPServerAdapter(container.tools)
    a2a = A2AServerAdapter(
        container.orchestrator,
        container.agents,
        operations=container.operations,
        task_ttl_seconds=settings.a2a_task_ttl_seconds,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def require_admin(
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        x_admin_user: str = Header(default="admin", alias="X-Admin-User"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> str:
        token = _bearer_token(authorization)
        if token:
            try:
                return verify_token(
                    settings.user_token_secret, token, expected_role="admin"
                ).subject
            except TokenError:
                pass
        if x_admin_token and secrets.compare_digest(x_admin_token, settings.admin_token):
            # A shared admin secret cannot prove the caller's self-reported name.
            # Keep the convenient header only in local development.
            return x_admin_user if not _is_production(settings) else "admin-token"
        raise HTTPException(status_code=401, detail="invalid admin token")

    def require_service(
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
        x_agent_name: str | None = Header(default=None, alias="X-Agent-Name"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Principal:
        token = _bearer_token(authorization) or x_service_token
        if token:
            try:
                return verify_token(settings.service_token, token, expected_role="service")
            except TokenError:
                if not _is_production(settings) and secrets.compare_digest(
                    token, settings.service_token
                ):
                    return Principal(
                        subject="development-service", role="service", agent=x_agent_name
                    )
        if not _is_production(settings):
            return Principal(subject="anonymous", role="service", agent=x_agent_name)
        raise HTTPException(status_code=401, detail="invalid service token")

    def require_user(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Principal | None:
        token = _bearer_token(authorization)
        if token:
            try:
                return verify_token(settings.user_token_secret, token, expected_role="user")
            except TokenError as exc:
                raise HTTPException(status_code=401, detail="invalid user token") from exc
        if _is_production(settings):
            raise HTTPException(status_code=401, detail="user authentication required")
        return None

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        started = time.perf_counter()
        trace_id = uuid4().hex
        raw_correlation_id = request.headers.get("X-Correlation-ID", "")
        correlation_id = (
            raw_correlation_id
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", raw_correlation_id)
            else ""
        )
        request.state.trace_id = trace_id
        request.state.correlation_id = correlation_id or None
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        if correlation_id:
            response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        )
        if _is_production(settings):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "trace_id=%s correlation_id=%s method=%s path=%s status=%s latency_ms=%.2f",
            trace_id,
            correlation_id or "-",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        static_file = Path(__file__).parent / "static" / "index.html"
        return FileResponse(static_file)

    @app.get("/admin", include_in_schema=False)
    async def admin_page() -> FileResponse:
        static_file = Path(__file__).parent / "static" / "admin.html"
        return FileResponse(static_file)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "shopagent-pro"}

    @app.get("/ready")
    async def ready() -> Response:
        checks = {}
        for name, component in {
            "memory": container.memory,
            "operations": container.operations,
            "knowledge": container.knowledge_repository,
            "commerce": container.commerce,
        }.items():
            try:
                checks[name] = bool(await component.health())
            except Exception:
                logger.exception("readiness check failed: %s", name)
                checks[name] = False
        payload = {
            "status": "ready" if all(checks.values()) else "not_ready",
            "storage": checks,
            "tools": {
                agent: container.tools.discover(agent)
                for agent in (
                    "product_agent",
                    "recommendation_agent",
                    "order_agent",
                    "after_sales_agent",
                )
            },
            "agents": container.agents.describe(),
        }
        return JSONResponse(payload, status_code=200 if all(checks.values()) else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        lines = ["# TYPE shopagent_tool_calls_total counter"]
        for name, values in container.tools.metrics().items():
            safe_name = name.replace(".", "_")
            lines.extend(
                [
                    f'shopagent_tool_calls_total{{tool="{safe_name}"}} {values["calls"]}',
                    f'shopagent_tool_failures_total{{tool="{safe_name}"}} {values["failures"]}',
                    f'shopagent_tool_latency_ms{{tool="{safe_name}"}} {values["avg_latency_ms"]}',
                    f'shopagent_tool_circuit_open{{tool="{safe_name}"}} {int(values["circuit_open"])}',
                ]
            )
        # High-level business metrics derived from the operations dashboard.
        board = await container.operations.dashboard()
        lines.append("# TYPE shopagent_interactions_total counter")
        lines.append(f"shopagent_interactions_total {board['total_interactions']}")
        lines.append("# TYPE shopagent_intent_total counter")
        for intent, count in board["intent_distribution"].items():
            lines.append(f'shopagent_intent_total{{intent="{intent}"}} {count}')
        lines.append("# TYPE shopagent_automation_rate gauge")
        lines.append(f"shopagent_automation_rate {board['automation_rate']}")
        lines.append("# TYPE shopagent_human_handoff_rate gauge")
        lines.append(f"shopagent_human_handoff_rate {board['handoff_rate']}")
        lines.append("# TYPE shopagent_human_handoffs_total counter")
        lines.append(f"shopagent_human_handoffs_total {board['human_handoffs']}")
        lines.append("# TYPE shopagent_agent_routed_total counter")
        for agent, count in board["agent_distribution"].items():
            lines.append(f'shopagent_agent_routed_total{{agent="{agent}"}} {count}')
        if board["average_rating"] is not None:
            lines.append("# TYPE shopagent_average_rating gauge")
            lines.append(f"shopagent_average_rating {board['average_rating']}")
        lines.append("# TYPE shopagent_pending_knowledge_candidates gauge")
        lines.append(
            f"shopagent_pending_knowledge_candidates {board['pending_knowledge_candidates']}"
        )
        lines.append("# TYPE shopagent_a2a_tasks_total counter")
        lines.append(f"shopagent_a2a_tasks_total {board['a2a_task_count']}")
        return "\n".join(lines) + "\n"

    @app.post("/mcp")
    async def mcp_endpoint(
        payload: dict,
        x_agent_name: str = Header(default="product_agent", alias="X-Agent-Name"),
        mcp_protocol_version: str | None = Header(default=None, alias="MCP-Protocol-Version"),
        principal: Principal = Depends(require_service),
    ) -> Response:
        if mcp_protocol_version and mcp_protocol_version != mcp.protocol_version:
            return JSONResponse(
                mcp._error(payload.get("id"), -32602, "Unsupported MCP protocol version"),
                status_code=400,
            )
        agent_name = principal.agent or x_agent_name
        if _is_production(settings) and not principal.agent:
            raise HTTPException(status_code=403, detail="service token has no agent identity")
        result = await mcp.handle(payload, agent_name=agent_name)
        if result is None:
            return Response(status_code=202)
        return JSONResponse(result)

    @app.get("/.well-known/agent-card.json")
    async def root_agent_card(request: Request, _: Principal = Depends(require_service)) -> dict:
        base_url = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
        return a2a.agent_card(base_url)

    @app.get("/a2a/{agent_name}/.well-known/agent-card.json")
    async def domain_agent_card(
        agent_name: str, request: Request, _: Principal = Depends(require_service)
    ) -> dict:
        try:
            base_url = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
            return a2a.agent_card(base_url, agent_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="agent not found") from exc

    @app.post("/a2a/message:send")
    async def a2a_send(
        payload: dict,
        request: Request,
        principal: Principal = Depends(require_service),
    ) -> JSONResponse:
        try:
            trace_id = _trusted_a2a_trace(payload, principal, request.state.trace_id)
            result = await a2a.send_message(payload, owner=principal.subject, trace_id=trace_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(result, media_type="application/a2a+json")

    @app.post("/a2a/{agent_name}/message:send")
    async def a2a_send_to_agent(
        agent_name: str,
        payload: dict,
        request: Request,
        principal: Principal = Depends(require_service),
    ) -> JSONResponse:
        if not container.agents.get(agent_name):
            raise HTTPException(status_code=404, detail="agent not found")
        if _is_production(settings) and principal.agent not in {
            agent_name,
            "shopagent_orchestrator",
        }:
            raise HTTPException(status_code=403, detail="service token cannot call this agent")
        try:
            trace_id = _trusted_a2a_trace(payload, principal, request.state.trace_id)
            result = await a2a.send_message(
                payload,
                target_agent=agent_name,
                owner=principal.subject,
                trace_id=trace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(result, media_type="application/a2a+json")

    @app.get("/a2a/tasks/{task_id}")
    async def a2a_get_task(
        task_id: str, principal: Principal = Depends(require_service)
    ) -> JSONResponse:
        task = await a2a.get_task(task_id, owner=principal.subject)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(task, media_type="application/a2a+json")

    @app.post("/api/v1/chat")
    async def chat(
        payload: ChatMessage,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
        principal: Principal | None = Depends(require_user),
    ):
        payload = _bind_chat_user(payload, principal)
        if idempotency_key:
            payload = payload.model_copy(
                update={"context": {**payload.context, "idempotency_key": idempotency_key}}
            )
        return await container.orchestrator.handle(payload, request.state.trace_id)

    @app.post("/api/v1/chat/stream")
    async def chat_stream(
        payload: ChatMessage,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
        principal: Principal | None = Depends(require_user),
    ) -> StreamingResponse:
        payload = _bind_chat_user(payload, principal)
        trace_id = request.state.trace_id
        if idempotency_key:
            payload = payload.model_copy(
                update={"context": {**payload.context, "idempotency_key": idempotency_key}}
            )

        async def events():
            yield _sse("trace", {"trace_id": trace_id})
            result = await container.orchestrator.handle(payload, trace_id)
            yield _sse("intent", result.intent.model_dump(mode="json"))
            for chunk in _chunks(result.answer, 18):
                yield _sse("message", {"chunk": chunk})
            yield _sse("done", result.model_dump(mode="json"))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/memory/{session_id}")
    async def get_memory(
        session_id: str,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        principal: Principal | None = Depends(require_user),
    ):
        user_id = _bound_user_id(x_user_id, principal)
        return await container.memory.get(user_id, session_id)

    @app.delete("/api/v1/memory/{session_id}", status_code=204)
    async def clear_memory(
        session_id: str,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        principal: Principal | None = Depends(require_user),
    ) -> None:
        user_id = _bound_user_id(x_user_id, principal)
        await container.memory.clear(user_id, session_id)

    @app.post("/api/v1/feedback", status_code=201)
    async def submit_feedback(
        payload: FeedbackRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
        principal: Principal | None = Depends(require_user),
    ) -> dict:
        user_id = _bound_user_id(payload.user_id, principal)
        try:
            feedback, candidate = await container.feedback.submit(
                trace_id=payload.trace_id,
                user_id=user_id,
                rating=payload.rating,
                comment=payload.comment,
                suggested_answer=payload.suggested_answer,
                idempotency_key=idempotency_key or f"feedback:{user_id}:{payload.trace_id}",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "feedback": feedback.model_dump(mode="json"),
            "knowledge_candidate": candidate.model_dump(mode="json") if candidate else None,
        }

    @app.get("/api/v1/admin/dashboard")
    async def operations_dashboard(_: str = Depends(require_admin)) -> dict:
        return await container.operations.dashboard()

    @app.get("/api/v1/admin/knowledge-candidates")
    async def list_knowledge_candidates(
        status: str | None = None, _: str = Depends(require_admin)
    ) -> list[dict]:
        candidates = await container.operations.list_candidates(status)
        return [item.model_dump(mode="json") for item in candidates]

    @app.get("/api/v1/admin/audit-events")
    async def list_audit_events(limit: int = 100, _: str = Depends(require_admin)) -> list[dict]:
        events = await container.operations.list_audit(limit)
        return [item.model_dump(mode="json") for item in events]

    @app.post("/api/v1/admin/knowledge-candidates/{candidate_id}/review")
    async def review_knowledge_candidate(
        candidate_id: str,
        payload: CandidateReviewRequest,
        reviewer: str = Depends(require_admin),
    ) -> dict:
        try:
            candidate = await container.feedback.review(
                candidate_id, approved=payload.approved, reviewer=reviewer
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return candidate.model_dump(mode="json")

    return app


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int):
    for index in range(0, len(text), size):
        yield text[index : index + size]


def _is_production(settings: Settings) -> bool:
    return settings.env.lower() in {"production", "prod"}


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    return None


def _bound_user_id(claimed_user_id: str | None, principal: Principal | None) -> str:
    if principal:
        if claimed_user_id and claimed_user_id != principal.subject:
            raise HTTPException(status_code=403, detail="user identity does not match access token")
        return principal.subject
    if not claimed_user_id:
        raise HTTPException(status_code=400, detail="user identity is required")
    return claimed_user_id


def _bind_chat_user(payload: ChatMessage, principal: Principal | None) -> ChatMessage:
    user_id = _bound_user_id(payload.user_id, principal)
    return (
        payload if user_id == payload.user_id else payload.model_copy(update={"user_id": user_id})
    )


def _trusted_a2a_trace(payload: dict, principal: Principal, fallback: str) -> str:
    message = payload.get("message") if isinstance(payload, dict) else None
    metadata = message.get("metadata") if isinstance(message, dict) else None
    proposed = metadata.get("traceId") if isinstance(metadata, dict) else None
    if (
        principal.subject != "anonymous"
        and isinstance(proposed, str)
        and re.fullmatch(r"[a-f0-9]{32}", proposed)
    ):
        return proposed
    return fallback


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("shopagent.api:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
