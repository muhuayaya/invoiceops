from __future__ import annotations

import os
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from invoiceops.adapters.in_memory import InMemoryRepository
from invoiceops.adapters.postgres import PostgresRepository
from invoiceops.adapters.sqlite import SQLiteRepository
from invoiceops.application.service import IdempotencyConflict, NotFound, TriageService
from invoiceops.batch import BatchProcessor, as_dict
from invoiceops.batch_store import PersistentBatchStore
from invoiceops.observability.logging import configure_logging
from invoiceops.retention import cleanup_in_memory
from invoiceops.security.auth import Principal, authenticate_seed_user, can, decode_token, issue_token
from invoiceops.domain.errors import DomainError, ValidationError

MAX_BATCH_BYTES = 10 * 1024 * 1024


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["supplier_portal", "email", "internal"] | None = None
    locale: Literal["zh", "en", "mixed"] | None = None


class ClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    source: Literal["email", "portal", "api", "manual"]
    text: str = Field(min_length=1, max_length=5000)
    taxonomy_version: Literal["invoiceops-v1"]
    metadata: Metadata = Field(default_factory=Metadata)


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[str] = Field(min_length=1)
    primary_queue: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


class AdminBatchDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_ids: list[UUID] = Field(min_length=1, max_length=100)


class AdminBatchOverwriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClassificationRequest] = Field(min_length=1, max_length=100)


class TokenRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or str(uuid4())


def create_app(service: TriageService | None = None) -> FastAPI:
    app = FastAPI(title="InvoiceOps API", version="1.0.0")
    batch_size_limit = int(os.getenv("INVOICEOPS_MAX_BATCH_BYTES", str(MAX_BATCH_BYTES)))
    cors_origins = [
        origin.strip()
        for origin in os.getenv(
            "INVOICEOPS_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Reviewer-Id"],
        expose_headers=["X-Trace-Id"],
    )
    data_db = os.getenv("INVOICEOPS_DATA_DB", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        repository = PostgresRepository(database_url)
    else:
        repository = SQLiteRepository(data_db) if data_db else InMemoryRepository()
    app.state.service = service or TriageService(repository=repository)
    batch_db = os.getenv("INVOICEOPS_BATCH_DB", "").strip()
    app.state.batches = BatchProcessor(app.state.service, PersistentBatchStore(batch_db) if batch_db else None)
    app.state.model_ready = app.state.service.classifier is not None
    app.state.request_metrics = {"total": 0, "errors": 0, "latencies_ms": []}
    app.state.logger = configure_logging()

    def repository_ready() -> bool:
        engine = getattr(app.state.service.repository, "engine", None)
        if engine is None:
            return True
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except Exception:
            return False
        return True

    def refresh_repository(request: Request) -> None:
        refresh = getattr(request.app.state.service.repository, "refresh", None)
        if callable(refresh):
            refresh()

    def not_ready_response(request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "code": "NOT_READY",
                "message": "模型或业务数据库尚未就绪",
                "trace_id": _trace_id(request),
            },
        )

    def record_request(status_code: int, duration_ms: float) -> None:
        metrics = app.state.request_metrics
        metrics["total"] += 1
        if status_code >= 400:
            metrics["errors"] += 1
        metrics["latencies_ms"].append(duration_ms)
        del metrics["latencies_ms"][:-1000]

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        started = perf_counter()
        supplied = request.headers.get("X-Trace-Id", "").strip()
        request.state.trace_id = supplied[:128] if supplied else str(uuid4())
        try:
            response = await call_next(request)
        except Exception as exc:
            request.app.state.logger.error(
                "unhandled exception",
                extra={"trace_id": _trace_id(request), "stage": "exception", "result_code": "500", "version": "api-v1"},
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            response = JSONResponse(
                status_code=500,
                content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "trace_id": _trace_id(request)},
            )
        duration_ms = (perf_counter() - started) * 1000
        record_request(response.status_code, duration_ms)
        response.headers["X-Trace-Id"] = request.state.trace_id
        app.state.logger.info(
            "request completed",
            extra={
                "trace_id": request.state.trace_id,
                "duration_ms": round(duration_ms, 3),
                "result_code": str(response.status_code),
                "stage": "request",
                "version": "api-v1",
            },
        )
        return response

    def principal(request: Request, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            request.app.state.service.record_security_event("authorization.denied", _trace_id(request), "anonymous", {"reason": "missing_bearer"})
            raise HTTPException(status_code=401, detail="Bearer token required")
        try:
            return decode_token(authorization[7:])
        except Exception as exc:
            request.app.state.service.record_security_event("authorization.denied", _trace_id(request), "anonymous", {"reason": "invalid_token"})
            raise HTTPException(status_code=401, detail="invalid token") from exc

    def require(request: Request, authorization: str | None, roles: set[str]) -> Principal:
        current = principal(request, authorization)
        if not can(current, roles):
            request.app.state.service.record_security_event("authorization.denied", _trace_id(request), current.username, {"reason": "insufficient_role", "required": sorted(roles)})
            raise HTTPException(status_code=403, detail="insufficient role")
        return current

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        details = [{"loc": error.get("loc", ()), "type": error.get("type", "validation_error")} for error in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": "request validation failed", "trace_id": _trace_id(request), "details": details},
        )

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_handler(request: Request, exc: IdempotencyConflict):
        return JSONResponse(status_code=409, content={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc), "trace_id": _trace_id(request)})

    @app.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": str(exc), "trace_id": _trace_id(request)})

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"code": "DOMAIN_VALIDATION_ERROR", "message": str(exc), "trace_id": _trace_id(request)})

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        code = "FORBIDDEN" if exc.status_code == 403 else "UNAUTHORIZED" if exc.status_code == 401 else "HTTP_ERROR"
        message = str(exc.detail) if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(status_code=exc.status_code, content={"code": code, "message": message, "trace_id": _trace_id(request)})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request.app.state.logger.error(
            "unhandled exception",
            extra={"trace_id": _trace_id(request), "stage": "exception", "result_code": "500", "version": "api-v1"},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "trace_id": _trace_id(request)},
        )

    @app.post("/api/v1/classifications")
    def classify(
        request: Request,
        payload: ClassificationRequest,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        if not app.state.model_ready or not repository_ready():
            return not_ready_response(request)
        refresh_repository(request)
        service: TriageService = request.app.state.service
        return service.classify(
            request_id=payload.request_id,
            source=payload.source,
            text=payload.text,
            taxonomy_version=payload.taxonomy_version,
            metadata=payload.metadata.model_dump(exclude_none=True),
            idempotency_key=idempotency_key,
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/auth/token")
    def token(payload: TokenRequest):
        current = authenticate_seed_user(payload.username, payload.password)
        if current is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return {"access_token": issue_token(current.username, current.role), "token_type": "bearer", "role": current.role}

    @app.get("/api/v1/reviews")
    def list_reviews(
        request: Request,
        authorization: str | None = Header(default=None),
        risk: Literal["low", "medium", "high"] | None = None,
        label_code: str | None = None,
        waiting_min_seconds: int | None = Query(default=None, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        require(request, authorization, {"observer", "reviewer"})
        refresh_repository(request)
        return {"items": request.app.state.service.list_reviews(risk, label_code, limit, waiting_min_seconds)}

    @app.get("/api/v1/reviews/{ticket_id}/history")
    def review_history(request: Request, ticket_id: UUID, authorization: str | None = Header(default=None)):
        require(request, authorization, {"observer", "reviewer"})
        refresh_repository(request)
        return {"items": request.app.state.service.review_history(ticket_id)}

    @app.post("/api/v1/admin/tickets")
    def admin_create_ticket(
        request: Request,
        payload: ClassificationRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        current = require(request, authorization, {"admin"})
        if not app.state.model_ready or not repository_ready():
            return not_ready_response(request)
        refresh_repository(request)
        return request.app.state.service.classify(
            request_id=payload.request_id,
            source=payload.source,
            text=payload.text,
            taxonomy_version=payload.taxonomy_version,
            metadata=payload.metadata.model_dump(exclude_none=True),
            idempotency_key=idempotency_key,
            trace_id=_trace_id(request),
            actor=current.username,
        )

    @app.get("/api/v1/admin/tickets")
    def admin_list_tickets(
        request: Request,
        authorization: str | None = Header(default=None),
        request_id: str | None = Query(default=None, max_length=128),
        risk: Literal["low", "medium", "high"] | None = None,
        status_filter: Literal["received", "classified", "needs_review", "reviewed"] | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        require(request, authorization, {"admin"})
        refresh_repository(request)
        return {"items": request.app.state.service.list_tickets(request_id, risk, status_filter, limit)}

    @app.delete("/api/v1/admin/tickets/{ticket_id}")
    def admin_delete_ticket(
        request: Request,
        ticket_id: UUID,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        current = require(request, authorization, {"admin"})
        refresh_repository(request)
        return request.app.state.service.delete_tickets(
            [ticket_id], idempotency_key=idempotency_key, actor=current.username, trace_id=_trace_id(request)
        )

    @app.post("/api/v1/admin/tickets/batch-delete")
    def admin_delete_tickets(
        request: Request,
        payload: AdminBatchDeleteRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        current = require(request, authorization, {"admin"})
        refresh_repository(request)
        return request.app.state.service.delete_tickets(
            payload.ticket_ids, idempotency_key=idempotency_key, actor=current.username, trace_id=_trace_id(request)
        )

    @app.post("/api/v1/admin/tickets/batch-overwrite")
    def admin_overwrite_tickets(
        request: Request,
        payload: AdminBatchOverwriteRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        current = require(request, authorization, {"admin"})
        if not app.state.model_ready or not repository_ready():
            return not_ready_response(request)
        refresh_repository(request)
        items = [
            {
                "request_id": item.request_id,
                "source": item.source,
                "text": item.text,
                "taxonomy_version": item.taxonomy_version,
                "metadata": item.metadata.model_dump(exclude_none=True),
            }
            for item in payload.items
        ]
        return request.app.state.service.overwrite_tickets(
            items, idempotency_key=idempotency_key, actor=current.username, trace_id=_trace_id(request)
        )

    @app.post("/api/v1/batches", status_code=status.HTTP_202_ACCEPTED)
    async def submit_batch(
        request: Request,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    ):
        require(request, authorization, {"reviewer"})
        if file.content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel", None}:
            raise ValidationError("batch file must be CSV")
        content = await file.read(batch_size_limit + 1)
        if len(content) > batch_size_limit:
            raise ValidationError(f"batch file exceeds {batch_size_limit} bytes")
        job, created = request.app.state.batches.create_with_result(content, idempotency_key)
        if not created:
            return as_dict(job) | {"trace_id": _trace_id(request)}
        if os.getenv("INVOICEOPS_CELERY_ENABLED", "false").lower() == "true":
            try:
                from apps.worker.main import celery_app
                if celery_app is None:
                    raise RuntimeError("celery is not installed")
                celery_app.send_task("apps.worker.main.process_batch", args=[str(job.batch_id), _trace_id(request)])
            except Exception:
                background_tasks.add_task(request.app.state.batches.process, job.batch_id)
        else:
            background_tasks.add_task(request.app.state.batches.process, job.batch_id)
        return as_dict(job) | {"trace_id": _trace_id(request)}

    @app.get("/api/v1/batches/{batch_id}")
    def get_batch(request: Request, batch_id: UUID, authorization: str | None = Header(default=None)):
        require(request, authorization, {"observer", "reviewer"})
        job = request.app.state.batches.get(batch_id)
        if job is None:
            raise NotFound("batch not found")
        return as_dict(job)

    @app.post("/api/v1/reviews/{ticket_id}/decisions", status_code=status.HTTP_201_CREATED)
    def append_review(
        request: Request,
        ticket_id: UUID,
        payload: ReviewDecisionRequest,
        authorization: str | None = Header(default=None),
    ):
        current = require(request, authorization, {"reviewer"})
        refresh_repository(request)
        return request.app.state.service.review(
            ticket_id=ticket_id,
            labels=payload.labels,
            primary_queue=payload.primary_queue,
            note=payload.note,
            reviewer_id=current.username,
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/taxonomies/current")
    def current_taxonomy(request: Request, authorization: str | None = Header(default=None)):
        require(request, authorization, {"observer", "reviewer"})
        return request.app.state.service.taxonomy.as_dict()

    @app.get("/api/v1/audit/{request_id}")
    def audit(request: Request, request_id: str, authorization: str | None = Header(default=None)):
        require(request, authorization, {"observer", "reviewer"})
        refresh_repository(request)
        return {"request_id": request_id, "events": request.app.state.service.audit(request_id)}

    @app.post("/api/v1/admin/retention/cleanup")
    def retention_cleanup(request: Request, authorization: str | None = Header(default=None)):
        require(request, authorization, {"admin"})
        result = cleanup_in_memory(request.app.state.service.repository)
        request.app.state.service.record_model_event("retention.cleanup", "retention-v1", actor="admin")
        return result

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request):
        if getattr(request.app.state, "service", None) is None or not getattr(request.app.state, "model_ready", False) or not repository_ready():
            return not_ready_response(request)
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        refresh_repository(request)
        repository = request.app.state.service.repository
        request_metrics = request.app.state.request_metrics
        latencies = sorted(request_metrics["latencies_ms"])
        p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1)) if latencies else 0
        p95 = latencies[p95_index] if latencies else 0.0
        total = request_metrics["total"]
        error_rate = request_metrics["errors"] / total if total else 0.0
        review_backlog = len(repository.list_reviews(None, None, 1_000_000))
        return "\n".join(
            [
                "# TYPE invoiceops_tickets_total gauge",
                f"invoiceops_tickets_total {len(repository.tickets)}",
                "# TYPE invoiceops_audit_events_total gauge",
                f"invoiceops_audit_events_total {len(repository.audit_events)}",
                "# TYPE invoiceops_error_rate gauge",
                f"invoiceops_error_rate {error_rate:.6f}",
                "# TYPE invoiceops_latency_p95_ms gauge",
                f"invoiceops_latency_p95_ms {p95:.3f}",
                "# TYPE invoiceops_review_backlog gauge",
                f"invoiceops_review_backlog {review_backlog}",
                "# TYPE invoiceops_llm_fallback_total counter",
                f"invoiceops_llm_fallback_total {getattr(request.app.state.service, 'llm_fallback_total', 0)}",
            ]
        ) + "\n"

    return app


app = create_app()
