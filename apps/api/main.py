"""InvoiceOps HTTP boundary; all authorization stays on the server."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import time
from uuid import uuid4
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from invoiceops.adapters import db as store
from invoiceops.application.batch import MAX_FILE_BYTES, InvalidBatch, parse_batch
from invoiceops.application.classifier import InvalidText, ModelUnavailable, classify, redact_text
from invoiceops.data.contract import load_taxonomy
from invoiceops.observability.monitoring import Metrics, RequestLimiter, language_slice
from invoiceops.security import auth, authorization


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=1024)


class ClassificationInput(BaseModel):
    text: str
    source: str = "manual"
    external_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=255)


class ReviewInput(BaseModel):
    expected_version: int
    decision: str
    final_labels: list[str]
    comment: str | None = None


class UserCreate(Credentials):
    role: str = Field(default="user", pattern="^(user|admin)$")


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(user|admin)$")
    active: bool | None = None


class TicketInput(BaseModel):
    text: str


class PredictionRevision(BaseModel):
    final_labels: list[str]
    comment: str | None = None


class BatchUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class BulkDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


def _user(user: store.User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "active": user.active}


def _prediction(session, ticket: store.Ticket):
    return session.scalar(select(store.Prediction).where(store.Prediction.ticket_id == ticket.id, store.Prediction.ticket_version == ticket.content_version).order_by(store.Prediction.created_at.desc()))


def _classification(session, ticket: store.Ticket) -> dict:
    prediction = _prediction(session, ticket)
    if prediction is None:
        return {"ticket_id": ticket.id, "external_id": ticket.external_id, "review_status": ticket.review_status, "state_version": ticket.state_version}
    review = session.scalar(select(store.ReviewDecision).where(store.ReviewDecision.prediction_id == prediction.id).order_by(store.ReviewDecision.created_at.desc()))
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "prediction_id": prediction.id,
        "probabilities": prediction.probabilities,
        "labels": prediction.labels,
        "final_labels": review.final_labels if review else prediction.labels,
        "taxonomy_version": prediction.taxonomy_version,
        "model_version": prediction.model_version,
        "threshold_version": prediction.threshold_version,
        "degraded": prediction.degraded,
        "fallback_reason": prediction.fallback_reason,
        "review_status": ticket.review_status,
        "review_reasons": prediction.review_reason.split("|") if prediction.review_reason else [],
        "state_version": ticket.state_version,
    }


def _load_runtime(path: str | None, role: str):
    if not path:
        return None
    try:
        from invoiceops.ml_runtime.supervisor import ModelSupervisor
        return ModelSupervisor(
            Path(path), role=role,
            taxonomy_path=os.getenv("INVOICEOPS_TAXONOMY", "configs/taxonomy.json"),
            device=os.getenv("INVOICEOPS_MODEL_DEVICE", "cpu"),
            inference_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_INFERENCE_TIMEOUT_SECONDS", "10")),
            startup_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_STARTUP_TIMEOUT_SECONDS", "180")),
            allow_unapproved_demo=os.getenv("INVOICEOPS_DEMO_MODE", "false").strip().lower() == "true",
        )
    except Exception:
        return None


def create_app(*, engine=None, primary=None, fallback=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        for candidate in (_app.state.primary, _app.state.fallback):
            start_model = getattr(candidate, "start", None)
            if start_model is not None:
                try:
                    await asyncio.to_thread(start_model)
                except Exception as exc:
                    logging.getLogger("invoiceops.models").warning(
                        "model startup unavailable role=%s error=%s: %s",
                        getattr(candidate, "role", "unknown"), type(exc).__name__, exc,
                    )
        try:
            yield
        finally:
            for candidate in (_app.state.primary, _app.state.fallback):
                close_model = getattr(candidate, "close", None)
                if close_model is not None:
                    await asyncio.to_thread(close_model)

    app = FastAPI(title="InvoiceOps", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine or store.get_engine(os.environ["INVOICEOPS_DATABASE_URL"])
    app.state.primary = primary if primary is not None else _load_runtime(os.getenv("INVOICEOPS_PRIMARY_MODEL"), "primary")
    app.state.fallback = fallback if fallback is not None else _load_runtime(os.getenv("INVOICEOPS_FALLBACK_MODEL"), "fallback")
    app.state.primary_failed = False
    app.state.demo_mode = os.getenv("INVOICEOPS_DEMO_MODE", "false").strip().lower() == "true"
    app.state.metrics = Metrics()
    app.state.auth_limiter = RequestLimiter(max_requests=10)
    app.state.auth_ip_limiter = RequestLimiter(max_requests=60)
    app.state.taxonomy_version, app.state.labels = load_taxonomy(os.getenv("INVOICEOPS_TAXONOMY", "configs/taxonomy.json"))

    @app.middleware("http")
    async def request_tracking(request: Request, call_next):
        request_id = str(uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("invoiceops.requests").info(
            "request_id=%s method=%s status=%d duration_ms=%.2f",
            request_id, request.method, response.status_code, (time.perf_counter() - start) * 1000,
        )
        return response

    def session():
        with store.new_session(app.state.engine) as current:
            yield current

    def actor(authorization: str | None = Header(default=None), current=Depends(session)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "login required")
        try:
            return auth.authenticate_token(current, authorization[7:])
        except auth.AuthenticationFailed as exc:
            raise HTTPException(401, str(exc)) from exc

    def admin(user=Depends(actor)):
        try:
            authorization.require_admin(user)
        except store.PermissionDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        return user

    @app.exception_handler(store.PermissionDenied)
    async def permission_error(_request, exc):
        return Response(content=str(exc), status_code=403)

    @app.exception_handler(store.NotFoundError)
    async def missing_error(_request, exc):
        return Response(content=str(exc), status_code=404)

    @app.exception_handler(store.StateConflict)
    async def conflict_error(_request, exc):
        return Response(content=str(exc), status_code=409)

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request, _exc):
        return Response(content="record conflicts with existing data", status_code=409)

    @app.exception_handler(InvalidText)
    async def text_error(_request, exc):
        return Response(content=str(exc), status_code=422)

    @app.exception_handler(InvalidBatch)
    async def batch_error(_request, exc):
        return Response(content=str(exc), status_code=422)

    @app.exception_handler(ModelUnavailable)
    async def model_error(_request, exc):
        return Response(content=str(exc), status_code=503)

    @app.get("/healthz")
    def healthz():
        return {"status": "alive"}

    @app.get("/readyz")
    def readyz(current=Depends(session)):
        try:
            current.execute(select(store.User.id).limit(1))
        except Exception:
            raise HTTPException(503, "database unavailable")
        primary_ready = bool(
            app.state.primary and getattr(app.state.primary, "serving_allowed", getattr(app.state.primary, "engineering_approved", False))
            and getattr(app.state.primary, "ready", True) and not app.state.primary_failed
        )
        fallback_ready = bool(
            app.state.fallback and getattr(app.state.fallback, "serving_allowed", getattr(app.state.fallback, "engineering_approved", False))
            and getattr(app.state.fallback, "ready", True)
        )
        if primary_ready:
            response = {"status": "ready", "model_version": app.state.primary.version, "degraded": False}
            if app.state.demo_mode:
                response["demo_mode"] = True
                response["engineering_approved"] = bool(getattr(app.state.primary, "engineering_approved", False))
            return response
        if fallback_ready:
            response = {"status": "degraded", "model_version": app.state.fallback.version, "degraded": True}
            if app.state.demo_mode:
                response["demo_mode"] = True
                response["engineering_approved"] = bool(getattr(app.state.fallback, "engineering_approved", False))
            return response
        raise HTTPException(503, "no approved model available")

    @app.post("/v1/auth/register")
    def register(body: Credentials, request: Request, current=Depends(session)):
        ip = request.client.host if request.client else "unknown"
        if (not app.state.auth_ip_limiter.allow("register:" + ip)
                or not app.state.auth_limiter.allow("register:" + body.email.strip().casefold())):
            raise HTTPException(429, "too many registration attempts")
        try:
            user = auth.register_user(current, body.email, body.password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current.commit()
        return _user(user)

    @app.post("/v1/auth/login")
    def login(body: Credentials, request: Request, current=Depends(session)):
        ip = request.client.host if request.client else "unknown"
        if (not app.state.auth_ip_limiter.allow("login:" + ip)
                or not app.state.auth_limiter.allow("login:" + body.email.strip().casefold())):
            raise HTTPException(429, "too many login attempts")
        try:
            user, token = auth.login_user(current, body.email, body.password)
        except auth.AuthenticationFailed as exc:
            raise HTTPException(401, str(exc)) from exc
        current.commit()
        return {"token": token, "user": _user(user)}

    @app.post("/v1/auth/logout")
    def logout(authorization: str | None = Header(default=None), user=Depends(actor), current=Depends(session)):
        auth.logout_user(current, authorization[7:])
        current.commit()
        return {"status": "logged_out"}

    @app.get("/v1/auth/me")
    def me(user=Depends(actor)):
        return _user(user)

    @app.get("/metrics")
    def metrics(_admin=Depends(admin)):
        return {"classifications": app.state.metrics.snapshot()}

    def run_classification(text: str, source: str):
        start = time.perf_counter()
        language = language_slice(text)
        try:
            result = classify(text, primary=app.state.primary, fallback=app.state.fallback, label_order=app.state.labels, taxonomy_version=app.state.taxonomy_version)
        except ModelUnavailable:
            app.state.primary_failed = True
            app.state.metrics.observe(model_version="unavailable", source=source, language=language, outcome="error", seconds=time.perf_counter() - start)
            raise
        app.state.primary_failed = result.degraded
        app.state.metrics.observe(model_version=result.model_version, source=source, language=language, outcome="degraded" if result.degraded else "ok", seconds=time.perf_counter() - start)
        return result

    def attach_prediction(current, ticket: store.Ticket, result) -> None:
        store.add_prediction(current, ticket.id, probabilities=result.probabilities, labels=list(result.labels), model_version=result.model_version, threshold_version=result.threshold_version, taxonomy_version=result.taxonomy_version, degraded=result.degraded, review_reason="|".join(result.review_reasons) or None, fallback_reason=result.fallback_reason)

    def classify_admin_ticket(current, ticket: store.Ticket) -> bool:
        """Classify a ticket saved by an administrator; keep it pending when no model can serve."""
        try:
            result = run_classification(ticket.redacted_text, "admin")
        except ModelUnavailable:
            return False
        attach_prediction(current, ticket, result)
        return True

    @app.post("/v1/classifications")
    def create_classification(body: ClassificationInput, user=Depends(actor), current=Depends(session)):
        if body.idempotency_key:
            prior = current.scalar(select(store.Ticket).where(store.Ticket.submitted_by == user.id, store.Ticket.idempotency_key == body.idempotency_key))
            if prior is not None:
                if prior.redacted_text != redact_text(body.text.strip()):
                    raise HTTPException(409, "idempotency key used for different text")
                return _classification(current, prior)
        source = body.source if body.source in ("manual", "api", "batch") else "other"
        result = run_classification(body.text, source)
        ticket = store.create_ticket(current, user.id, result.redacted_text, idempotency_key=body.idempotency_key, external_id=body.external_id)
        if ticket.redacted_text != result.redacted_text:
            raise HTTPException(409, "idempotency key used for different text")
        if _prediction(current, ticket) is None:
            attach_prediction(current, ticket, result)
        current.commit()
        return _classification(current, ticket)

    @app.get("/v1/classifications")
    def classifications(user=Depends(actor), current=Depends(session)):
        return [_classification(current, ticket) for ticket in store.list_submission_history(current, user)]

    def batch_status(current, batch: store.BatchJob) -> dict:
        counts = Counter(item.status for item in current.scalars(select(store.BatchItem).where(store.BatchItem.batch_id == batch.id)))
        return {"id": batch.id, "name": batch.name, "status": batch.status, "total": batch.total_rows, "succeeded": counts["succeeded"], "failed": counts["failed"], "pending": counts["pending"]}

    @app.post("/v1/batches", status_code=202)
    async def create_batch(file: UploadFile = File(...), user=Depends(actor), current=Depends(session)):
        content = bytearray()
        while chunk := await file.read(64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise InvalidBatch("CSV exceeds maximum file size")
        rows = parse_batch(bytes(content), max_bytes=MAX_FILE_BYTES)
        batch = store.BatchJob(submitted_by=user.id, total_rows=len(rows), default_model_version=getattr(app.state.primary, "version", None))
        current.add(batch)
        current.flush()
        for row in rows:
            current.add(store.BatchItem(batch_id=batch.id, row_number=row.row_number, external_id=row.external_id, redacted_text=redact_text(row.text) if row.text else None, status="failed" if row.error_code else "pending", error_code=row.error_code, error_reason=row.error_reason))
        current.commit()
        try:
            from apps.worker.tasks import process_batch
            process_batch.delay(batch.id)
        except Exception:
            pass  # Periodic worker recovery will pick up pending rows.
        return batch_status(current, batch)

    @app.get("/v1/batches")
    def batches(user=Depends(actor), current=Depends(session)):
        query = select(store.BatchJob).order_by(store.BatchJob.created_at.desc())
        if user.role != "admin":
            query = query.where(store.BatchJob.submitted_by == user.id)
        return [batch_status(current, batch) for batch in current.scalars(query)]

    @app.get("/v1/batches/{batch_id}")
    def get_batch(batch_id: str, user=Depends(actor), current=Depends(session)):
        return batch_status(current, store.get_batch_for_download(current, batch_id, user))

    def output_csv(current, batch: store.BatchJob, errors_only: bool = False) -> str:
        columns = ["row_number", "external_id", "labels", *app.state.labels, "review_status", "review_reasons", "model_version", "threshold_version", "degraded", "fallback_reason", "row_status", "error_code", "error_reason"]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for item in current.scalars(select(store.BatchItem).where(store.BatchItem.batch_id == batch.id).order_by(store.BatchItem.row_number)):
            if errors_only and item.status != "failed":
                continue
            ticket = current.get(store.Ticket, item.ticket_id) if item.ticket_id else None
            prediction = _prediction(current, ticket) if ticket else None
            row = {"row_number": item.row_number, "external_id": item.external_id or "", "labels": "|".join(prediction.labels) if prediction else "", "review_status": ticket.review_status if ticket else "", "review_reasons": prediction.review_reason or "" if prediction else "", "model_version": item.actual_model_version or "", "threshold_version": item.threshold_version or "", "degraded": item.degraded, "fallback_reason": prediction.fallback_reason if prediction else "", "row_status": item.status, "error_code": item.error_code or "", "error_reason": item.error_reason or ""}
            for label in app.state.labels:
                row[label] = prediction.probabilities.get(label, "") if prediction else ""
            writer.writerow(row)
        return output.getvalue()

    @app.get("/v1/batches/{batch_id}/download")
    def download(batch_id: str, user=Depends(actor), current=Depends(session)):
        batch = store.get_batch_for_download(current, batch_id, user)
        return Response(output_csv(current, batch), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{batch.id}.csv"'})

    @app.get("/v1/batches/{batch_id}/errors")
    def download_errors(batch_id: str, user=Depends(actor), current=Depends(session)):
        batch = store.get_batch_for_download(current, batch_id, user)
        return Response(output_csv(current, batch, True), media_type="text/csv; charset=utf-8")

    @app.get("/v1/reviews/queue")
    def review_queue(user=Depends(actor), current=Depends(session)):
        return [{**_classification(current, ticket), "redacted_text": ticket.redacted_text, "submitted_by": ticket.submitted_by} for ticket in store.list_review_queue(current, user)]

    @app.get("/v1/reviews/history")
    def review_history(user=Depends(actor), current=Depends(session)):
        return [{"ticket_id": row.ticket_id, "reviewer_id": row.reviewer_id, "decision": row.decision, "final_labels": row.final_labels, "original_labels": row.original_labels, "comment": row.comment, "created_at": row.created_at} for row in store.list_review_history(current, user)]

    @app.post("/v1/reviews/{ticket_id}")
    def submit_review(ticket_id: str, body: ReviewInput, user=Depends(actor), current=Depends(session)):
        if body.decision not in ("confirmed", "corrected") or len(body.final_labels) != len(set(body.final_labels)) or set(body.final_labels) - set(app.state.labels):
            raise HTTPException(422, "invalid review labels or decision")
        ticket = current.get(store.Ticket, ticket_id)
        if ticket is None:
            raise store.NotFoundError("ticket not found")
        authorization.can_review(user, ticket)
        row = store.submit_review(current, ticket_id, user.id, expected_version=body.expected_version, decision=body.decision, final_labels=body.final_labels, comment=body.comment)
        current.commit()
        return {"ticket_id": row.ticket_id, "decision": row.decision, "final_labels": row.final_labels, "version": row.version}

    @app.get("/v1/admin/users")
    def users(_admin=Depends(admin), current=Depends(session)):
        return [_user(user) for user in current.scalars(select(store.User).order_by(store.User.created_at))]

    @app.post("/v1/admin/users")
    def add_user(body: UserCreate, administrator=Depends(admin), current=Depends(session)):
        try:
            user = auth.create_user(current, administrator.id, body.email, body.password, role=body.role)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current.commit()
        return _user(user)

    @app.patch("/v1/admin/users/{user_id}")
    def update_user(user_id: str, body: UserUpdate, administrator=Depends(admin), current=Depends(session)):
        if body.role is not None:
            auth.set_user_role(current, administrator.id, user_id, body.role)
        if body.active is not None:
            auth.set_user_active(current, administrator.id, user_id, body.active)
        current.commit()
        user = current.get(store.User, user_id)
        return _user(user)

    @app.delete("/v1/admin/users/{user_id}")
    def remove_user(user_id: str, administrator=Depends(admin), current=Depends(session)):
        auth.delete_user(current, administrator.id, user_id)
        current.commit()
        return {"deleted": user_id}

    def remove_many(current, administrator, ids: list[str], model, delete_one):
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "duplicate IDs are not allowed")
        existing = set(current.scalars(select(model.id).where(model.id.in_(ids))))
        if existing != set(ids):
            raise HTTPException(404, "one or more records were not found")
        for object_id in ids:
            delete_one(current, object_id, administrator.id)
        current.commit()
        return {"deleted": ids, "count": len(ids)}

    @app.get("/v1/admin/tickets")
    def tickets(_admin=Depends(admin), current=Depends(session)):
        return [{**_classification(current, ticket), "redacted_text": ticket.redacted_text, "submitted_by": ticket.submitted_by, "content_version": ticket.content_version} for ticket in current.scalars(select(store.Ticket).order_by(store.Ticket.created_at.desc()))]

    @app.delete("/v1/admin/tickets")
    def remove_tickets(body: BulkDelete, administrator=Depends(admin), current=Depends(session)):
        return remove_many(current, administrator, body.ids, store.Ticket, store.delete_ticket)

    @app.post("/v1/admin/tickets")
    def add_ticket(body: TicketInput, administrator=Depends(admin), current=Depends(session)):
        if not body.text.strip():
            raise HTTPException(422, "text must not be empty")
        ticket = store.create_ticket(current, administrator.id, redact_text(body.text.strip()), review_status="pending_reclassification")
        classify_admin_ticket(current, ticket)
        current.commit()
        return _classification(current, ticket)

    @app.patch("/v1/admin/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, body: TicketInput, administrator=Depends(admin), current=Depends(session)):
        if not body.text.strip():
            raise HTTPException(422, "text must not be empty")
        ticket = store.revise_ticket(current, ticket_id, administrator.id, redact_text(body.text.strip()))
        if ticket.review_status == "pending_reclassification":
            classify_admin_ticket(current, ticket)
        current.commit()
        return _classification(current, ticket)

    @app.post("/v1/admin/tickets/{ticket_id}/reclassify")
    def reclassify_ticket(ticket_id: str, administrator=Depends(admin), current=Depends(session)):
        ticket = current.get(store.Ticket, ticket_id)
        if ticket is None:
            raise store.NotFoundError("ticket not found")
        if ticket.review_status != "pending_reclassification":
            raise store.StateConflict("ticket is not pending reclassification")
        result = run_classification(ticket.redacted_text, "admin")
        attach_prediction(current, ticket, result)
        store.audit(current, administrator.id, "ticket.reclassify", "ticket", ticket.id, {"version": ticket.content_version, "model_version": result.model_version})
        current.commit()
        return _classification(current, ticket)

    @app.delete("/v1/admin/tickets/{ticket_id}")
    def remove_ticket(ticket_id: str, administrator=Depends(admin), current=Depends(session)):
        store.delete_ticket(current, ticket_id, administrator.id)
        current.commit()
        return {"deleted": ticket_id}

    @app.patch("/v1/admin/predictions/{prediction_id}")
    def revise_prediction(prediction_id: str, body: PredictionRevision, administrator=Depends(admin), current=Depends(session)):
        if len(body.final_labels) != len(set(body.final_labels)):
            raise HTTPException(422, "duplicate labels")
        if set(body.final_labels) - set(app.state.labels):
            raise HTTPException(422, "unknown labels")
        row = store.admin_revise_prediction(current, prediction_id, administrator.id, body.final_labels, body.comment)
        current.commit()
        return {"ticket_id": row.ticket_id, "decision": row.decision, "final_labels": row.final_labels}

    @app.delete("/v1/admin/predictions/{prediction_id}")
    def remove_prediction(prediction_id: str, administrator=Depends(admin), current=Depends(session)):
        store.delete_prediction(current, prediction_id, administrator.id)
        current.commit()
        return {"deleted": prediction_id}

    @app.delete("/v1/admin/predictions")
    def remove_predictions(body: BulkDelete, administrator=Depends(admin), current=Depends(session)):
        return remove_many(current, administrator, body.ids, store.Prediction, store.delete_prediction)

    @app.delete("/v1/admin/batches/{batch_id}")
    def remove_batch(batch_id: str, administrator=Depends(admin), current=Depends(session)):
        store.delete_batch(current, batch_id, administrator.id)
        current.commit()
        return {"deleted": batch_id}

    @app.delete("/v1/admin/batches")
    def remove_batches(body: BulkDelete, administrator=Depends(admin), current=Depends(session)):
        return remove_many(current, administrator, body.ids, store.BatchJob, store.delete_batch)

    @app.get("/v1/admin/batches")
    def admin_batches(_admin=Depends(admin), current=Depends(session)):
        return [batch_status(current, batch) for batch in current.scalars(select(store.BatchJob).order_by(store.BatchJob.created_at.desc()))]

    @app.patch("/v1/admin/batches/{batch_id}")
    def revise_batch(batch_id: str, body: BatchUpdate, administrator=Depends(admin), current=Depends(session)):
        batch = current.get(store.BatchJob, batch_id)
        if batch is None:
            raise store.NotFoundError("batch not found")
        batch.name = body.name
        store.audit(current, administrator.id, "batch.rename", "batch", batch_id, {"name": body.name})
        current.commit()
        return batch_status(current, batch)

    @app.get("/v1/admin/history")
    def audit_history(_admin=Depends(admin), current=Depends(session)):
        events = [{"id": event.id, "actor_id": event.actor_id, "action": event.action, "object_type": event.object_type, "object_id": event.object_id, "details": event.details, "created_at": event.created_at} for event in current.scalars(select(store.AuditEvent).order_by(store.AuditEvent.created_at.desc()).limit(500))]
        predictions = [{"id": row.id, "ticket_id": row.ticket_id, "ticket_version": row.ticket_version, "submitted_by": row.submitted_by, "probabilities": row.probabilities, "labels": row.labels, "model_version": row.model_version, "threshold_version": row.threshold_version, "taxonomy_version": row.taxonomy_version, "degraded": row.degraded, "fallback_reason": row.fallback_reason, "review_reason": row.review_reason, "created_at": row.created_at} for row in current.scalars(select(store.PredictionHistory).order_by(store.PredictionHistory.created_at.desc()).limit(500))]
        reviews = [{"id": row.id, "ticket_id": row.ticket_id, "prediction_id": row.prediction_id, "submitted_by": row.submitted_by, "reviewer_id": row.reviewer_id, "decision": row.decision, "final_labels": row.final_labels, "original_labels": row.original_labels, "comment": row.comment, "created_at": row.created_at} for row in current.scalars(select(store.ReviewDecision).order_by(store.ReviewDecision.created_at.desc()).limit(500))]
        batch_items = [{"id": row.id, "batch_id": row.batch_id, "row_number": row.row_number, "external_id": row.external_id, "ticket_id": row.ticket_id, "status": row.status, "error_code": row.error_code, "error_reason": row.error_reason, "actual_model_version": row.actual_model_version, "threshold_version": row.threshold_version, "degraded": row.degraded, "deleted_by": row.deleted_by, "archived_at": row.archived_at} for row in current.scalars(select(store.BatchItemHistory).order_by(store.BatchItemHistory.archived_at.desc()).limit(500))]
        return {"audit_events": events, "prediction_history": predictions, "review_decisions": reviews, "batch_item_history": batch_items}

    return app


app = create_app() if os.getenv("INVOICEOPS_DATABASE_URL") else FastAPI(title="InvoiceOps (unconfigured)")
