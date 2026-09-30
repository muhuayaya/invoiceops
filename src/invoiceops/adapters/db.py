"""PostgreSQL persistence and the small set of atomic business writes.

Historical rows deliberately have no foreign keys to activity tables or users.
Callers must pass already redacted text; raw secrets never belong in snapshots.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="user_role_valid"),)


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submitted_by: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(255))
    redacted_text: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("review_status IN ('not_required', 'pending_review', 'confirmed', 'corrected', 'pending_reclassification')", name="ticket_status_valid"),
        UniqueConstraint("submitted_by", "idempotency_key", name="ticket_submitter_idempotency_unique"),
    )


class TicketVersion(Base):
    __tablename__ = "ticket_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    redacted_text: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("ticket_id", "version"),)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_version: Mapped[int] = mapped_column(Integer, nullable=False)
    probabilities: Mapped[dict] = mapped_column(JSON, nullable=False)
    labels: Mapped[list] = mapped_column(JSON, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    threshold_version: Mapped[str] = mapped_column(String(128), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(255))
    fallback_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PredictionHistory(Base):
    __tablename__ = "prediction_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    ticket_version: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(36), nullable=False)
    probabilities: Mapped[dict] = mapped_column(JSON, nullable=False)
    labels: Mapped[list] = mapped_column(JSON, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    threshold_version: Mapped[str] = mapped_column(String(128), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(255))
    fallback_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    prediction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(36), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    final_labels: Mapped[list] = mapped_column(JSON, nullable=False)
    original_labels: Mapped[list] = mapped_column(JSON, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("ticket_id", "version"), CheckConstraint("decision IN ('confirmed', 'corrected')", name="review_decision_valid"))


class BatchJob(Base):
    __tablename__ = "batch_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str | None] = mapped_column(String(255))
    submitted_by: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    default_model_version: Mapped[str | None] = mapped_column(String(128))
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BatchItem(Base):
    __tablename__ = "batch_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    redacted_text: Mapped[str | None] = mapped_column(Text)
    ticket_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tickets.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    error_reason: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    actual_model_version: Mapped[str | None] = mapped_column(String(128))
    threshold_version: Mapped[str | None] = mapped_column(String(128))
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("batch_id", "row_number"),)


class BatchItemHistory(Base):
    __tablename__ = "batch_item_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    ticket_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_reason: Mapped[str | None] = mapped_column(String(255))
    actual_model_version: Mapped[str | None] = mapped_column(String(128))
    threshold_version: Mapped[str | None] = mapped_column(String(128))
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deleted_by: Mapped[str] = mapped_column(String(36), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ModelRelease(Base):
    __tablename__ = "model_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_version: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    model_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    threshold_version: Mapped[str] = mapped_column(String(128), nullable=False)
    engineering_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    business_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    release_role: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def get_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)


def new_session(engine) -> Session:
    return sessionmaker(bind=engine, expire_on_commit=False)()


class NotFoundError(ValueError):
    pass


class PermissionDenied(PermissionError):
    pass


class StateConflict(ValueError):
    pass


def audit(session: Session, actor_id: str, action: str, object_type: str, object_id: str, details: dict | None = None) -> None:
    session.add(AuditEvent(actor_id=actor_id, action=action, object_type=object_type, object_id=object_id, details=details or {}))


def create_ticket(session: Session, submitted_by: str, redacted_text: str, *, review_status: str = "pending_review", idempotency_key: str | None = None, external_id: str | None = None) -> Ticket:
    if idempotency_key is not None:
        existing = session.scalar(select(Ticket).where(Ticket.submitted_by == submitted_by, Ticket.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    ticket = Ticket(submitted_by=submitted_by, redacted_text=redacted_text, review_status=review_status, idempotency_key=idempotency_key, external_id=external_id)
    if idempotency_key is None:
        session.add(ticket)
        session.flush()
    else:
        try:
            with session.begin_nested():
                session.add(ticket)
                session.flush()
        except IntegrityError:
            existing = session.scalar(select(Ticket).where(Ticket.submitted_by == submitted_by, Ticket.idempotency_key == idempotency_key))
            if existing is None:
                raise
            return existing
    session.add(TicketVersion(ticket_id=ticket.id, version=1, redacted_text=redacted_text, actor_id=submitted_by))
    audit(session, submitted_by, "ticket.create", "ticket", ticket.id)
    return ticket


def revise_ticket(session: Session, ticket_id: str, actor_id: str, redacted_text: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError("ticket not found")
    if ticket.redacted_text != redacted_text:
        ticket.content_version += 1
        ticket.state_version += 1
        ticket.redacted_text = redacted_text
        ticket.review_status = "pending_reclassification"
        ticket.updated_at = utcnow()
        session.add(TicketVersion(ticket_id=ticket.id, version=ticket.content_version, redacted_text=redacted_text, actor_id=actor_id))
        audit(session, actor_id, "ticket.revise", "ticket", ticket.id, {"version": ticket.content_version})
    return ticket


def add_prediction(session: Session, ticket_id: str, *, probabilities: dict, labels: list, model_version: str, threshold_version: str, taxonomy_version: str, degraded: bool = False, review_reason: str | None = None, fallback_reason: str | None = None) -> Prediction:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError("ticket not found")
    if degraded and not fallback_reason:
        raise ValueError("degraded prediction requires fallback_reason")
    prediction = Prediction(ticket_id=ticket_id, ticket_version=ticket.content_version, probabilities=probabilities, labels=labels, model_version=model_version, threshold_version=threshold_version, taxonomy_version=taxonomy_version, degraded=degraded, review_reason=review_reason, fallback_reason=fallback_reason)
    session.add(prediction)
    session.flush()
    session.add(PredictionHistory(id=prediction.id, ticket_id=ticket_id, ticket_version=ticket.content_version, submitted_by=ticket.submitted_by, probabilities=probabilities, labels=labels, model_version=model_version, threshold_version=threshold_version, taxonomy_version=taxonomy_version, degraded=degraded, review_reason=review_reason, fallback_reason=fallback_reason, created_at=prediction.created_at))
    ticket.review_status = "pending_review" if review_reason or degraded else "not_required"
    ticket.state_version += 1
    ticket.updated_at = utcnow()
    audit(session, ticket.submitted_by, "prediction.create", "prediction", prediction.id, {"ticket_id": ticket_id})
    return prediction


def submit_review(session: Session, ticket_id: str, reviewer_id: str, *, expected_version: int, decision: str, final_labels: list, comment: str | None = None) -> ReviewDecision:
    if decision not in ("confirmed", "corrected"):
        raise ValueError("invalid review decision")
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError("ticket not found")
    prediction = session.scalars(select(Prediction).where(Prediction.ticket_id == ticket_id, Prediction.ticket_version == ticket.content_version).order_by(Prediction.created_at.desc())).first()
    if prediction is None:
        raise StateConflict("current ticket version has no prediction")
    changed = session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.state_version == expected_version, Ticket.review_status == "pending_review").values(review_status=decision, state_version=Ticket.state_version + 1, updated_at=utcnow()))
    if changed.rowcount != 1:
        raise StateConflict("review state changed")
    row = ReviewDecision(ticket_id=ticket_id, prediction_id=prediction.id, submitted_by=ticket.submitted_by, reviewer_id=reviewer_id, version=expected_version + 1, decision=decision, final_labels=final_labels, original_labels=prediction.labels, model_version=prediction.model_version, review_reason=prediction.review_reason, comment=comment)
    session.add(row)
    audit(session, reviewer_id, "review.submit", "ticket", ticket_id, {"decision": decision, "version": expected_version + 1})
    return row



def admin_revise_prediction(session: Session, prediction_id: str, actor_id: str, final_labels: list, comment: str | None = None) -> ReviewDecision:
    actor = session.get(User, actor_id)
    if actor is None:
        raise PermissionDenied("administrator required")
    from invoiceops.security.authorization import require_admin

    require_admin(actor)
    prediction = session.get(Prediction, prediction_id)
    if prediction is None:
        raise NotFoundError("prediction not found")
    ticket = session.get(Ticket, prediction.ticket_id)
    if ticket is None:
        raise NotFoundError("ticket not found")
    if prediction.ticket_version != ticket.content_version:
        raise StateConflict("prediction belongs to an earlier ticket version")
    expected_version = ticket.state_version
    changed = session.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.state_version == expected_version).values(review_status="corrected", state_version=Ticket.state_version + 1, updated_at=utcnow()))
    if changed.rowcount != 1:
        raise StateConflict("classification state changed")
    row = ReviewDecision(ticket_id=ticket.id, prediction_id=prediction.id, submitted_by=ticket.submitted_by, reviewer_id=actor_id, version=expected_version + 1, decision="corrected", final_labels=final_labels, original_labels=prediction.labels, model_version=prediction.model_version, review_reason=prediction.review_reason, comment=comment)
    session.add(row)
    audit(session, actor_id, "prediction.revise", "prediction", prediction_id, {"ticket_id": ticket.id, "version": expected_version + 1})
    return row


def delete_ticket(session: Session, ticket_id: str, actor_id: str) -> None:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError("ticket not found")
    audit(session, actor_id, "ticket.delete", "ticket", ticket_id, {"submitted_by": ticket.submitted_by, "content_version": ticket.content_version})
    session.delete(ticket)


def delete_prediction(session: Session, prediction_id: str, actor_id: str) -> None:
    prediction = session.get(Prediction, prediction_id)
    if prediction is None:
        raise NotFoundError("prediction not found")
    audit(session, actor_id, "prediction.delete", "prediction", prediction_id, {"ticket_id": prediction.ticket_id})
    ticket = session.get(Ticket, prediction.ticket_id)
    if ticket is not None and ticket.content_version == prediction.ticket_version:
        ticket.review_status = "pending_reclassification"
        ticket.state_version += 1
        ticket.updated_at = utcnow()
    session.delete(prediction)


def delete_batch(session: Session, batch_id: str, actor_id: str) -> None:
    batch = session.get(BatchJob, batch_id)
    if batch is None:
        raise NotFoundError("batch not found")
    items = list(session.scalars(select(BatchItem).where(BatchItem.batch_id == batch_id)))
    for item in items:
        session.add(BatchItemHistory(
            id=item.id, batch_id=item.batch_id, row_number=item.row_number, external_id=item.external_id,
            ticket_id=item.ticket_id, status=item.status, error_code=item.error_code,
            error_reason=item.error_reason, actual_model_version=item.actual_model_version,
            threshold_version=item.threshold_version, degraded=item.degraded, deleted_by=actor_id,
        ))
    audit(session, actor_id, "batch.delete", "batch", batch_id, {"submitted_by": batch.submitted_by, "item_count": len(items)})
    session.delete(batch)


def list_submission_history(session: Session, actor: User):
    query = select(Ticket).order_by(Ticket.created_at.desc())
    if actor.role != "admin":
        query = query.where(Ticket.submitted_by == actor.id)
    return list(session.scalars(query))


def list_review_queue(session: Session, actor: User):
    return list(session.scalars(select(Ticket).where(Ticket.review_status == "pending_review").order_by(Ticket.created_at)))


def list_review_history(session: Session, actor: User):
    query = select(ReviewDecision).order_by(ReviewDecision.created_at.desc())
    if actor.role != "admin":
        query = query.where(ReviewDecision.reviewer_id == actor.id)
    return list(session.scalars(query))


def get_batch_for_download(session: Session, batch_id: str, actor: User) -> BatchJob:
    batch = session.get(BatchJob, batch_id)
    if batch is None:
        raise NotFoundError("batch not found")
    from invoiceops.security.authorization import can_download_batch

    can_download_batch(actor, batch)
    return batch
