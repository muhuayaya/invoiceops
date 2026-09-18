from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from invoiceops.adapters.in_memory import IdempotencyRecord, InMemoryRepository
from invoiceops.adapters.sqlalchemy import (
    AuditEventRow,
    IdempotencyRow,
    LLMSuggestionRow,
    OutboxEventRow,
    PredictionRow,
    ReviewDecisionRow,
    TicketRow,
    create_schema,
)
from invoiceops.domain.entities import AuditEvent, OutboxEvent, Prediction, ReviewDecision, Ticket, TicketStatus


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresRepository(InMemoryRepository):
    """SQLAlchemy-backed repository for Compose/PostgreSQL deployments.

    The domain service still uses the same repository contract as the fast
    in-memory and local SQLite adapters. Each mutation is committed before the
    worker checkpoint can advance, and unique database keys preserve
    idempotency across process restarts.
    """

    def __init__(self, database_url: str) -> None:
        super().__init__()
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
        if database_url.startswith("sqlite"):
            create_schema(self.engine)
        self._load()

    def _load(self) -> None:
        snapshot = InMemoryRepository()
        with Session(self.engine) as session:
            for row in session.scalars(select(TicketRow)):
                snapshot.add_ticket(
                    Ticket(
                        id=UUID(row.id), request_id=row.request_id, source=row.source, text=row.text,
                        taxonomy_version=row.taxonomy_version, metadata=dict(row.metadata_json or {}),
                        language=row.language, risk=row.risk, status=TicketStatus(row.status),
                        trace_id=row.trace_id, created_at=_utc(row.created_at),
                    )
                )
            for row in session.scalars(select(PredictionRow)):
                snapshot.add_prediction(
                    Prediction(
                        id=UUID(row.id), ticket_id=UUID(row.ticket_id), decision=TicketStatus(row.decision),
                        labels=tuple((str(code), float(score)) for code, score in (row.labels or [])),
                        reason_codes=tuple(row.reason_codes or []), route_primary=row.route_primary,
                        route_collaborators=tuple(row.route_collaborators or []), route_version=row.route_version,
                        model_version=row.model_version, threshold_version=row.threshold_version,
                        taxonomy_version=row.taxonomy_version, language=row.language,
                        inference_ms=float(row.inference_ms), trace_id=row.trace_id,
                        created_at=_utc(row.created_at),
                    )
                )
            for row in session.scalars(select(ReviewDecisionRow).order_by(ReviewDecisionRow.ticket_id, ReviewDecisionRow.revision)):
                snapshot.add_review(
                    ReviewDecision(
                        id=UUID(row.id), ticket_id=UUID(row.ticket_id), revision=row.revision,
                        labels=tuple(row.labels or []), primary_queue=row.primary_queue, note=row.note,
                        reviewer_id=row.reviewer_id, training_candidate=row.training_candidate,
                        created_at=_utc(row.created_at), trace_id=row.trace_id,
                    )
                )
            for row in session.scalars(select(AuditEventRow).order_by(AuditEventRow.occurred_at)):
                snapshot.add_audit(
                    AuditEvent(
                        event_id=UUID(row.event_id), event_type=row.event_type, request_id=row.request_id,
                        trace_id=row.trace_id, actor=row.actor, subject=row.subject, payload=dict(row.payload or {}),
                        occurred_at=_utc(row.occurred_at), event_version=row.event_version,
                    )
                )
            for row in session.scalars(select(OutboxEventRow).order_by(OutboxEventRow.created_at)):
                snapshot.add_outbox(
                    OutboxEvent(
                        event_id=UUID(row.event_id), event_type=row.event_type, aggregate_id=row.aggregate_id,
                        payload=dict(row.payload or {}), created_at=_utc(row.created_at),
                        published_at=_utc(row.published_at) if row.published_at else None,
                    )
                )
            for row in session.scalars(select(LLMSuggestionRow).order_by(LLMSuggestionRow.created_at)):
                snapshot.llm_suggestions.append(
                    {
                        "ticket_id": row.ticket_id, "labels": row.labels, "rationale": row.rationale,
                        "confidence": row.confidence, "provider": row.provider, "model": row.model,
                        "prompt_version": row.prompt_version, "cost_usd": row.cost_usd,
                    }
                )
            for row in session.scalars(select(IdempotencyRow)):
                snapshot.idempotency[row.key] = IdempotencyRecord(
                    row.fingerprint, dict(row.response or {}), UUID(row.ticket_id) if row.ticket_id else None
                )

        self.tickets = snapshot.tickets
        self.tickets_by_request = snapshot.tickets_by_request
        self.predictions = snapshot.predictions
        self.reviews = snapshot.reviews
        self.audit_events = snapshot.audit_events
        self.outbox_events = snapshot.outbox_events
        self.llm_suggestions = snapshot.llm_suggestions
        self.idempotency = snapshot.idempotency

    def refresh(self) -> None:
        """Refresh the in-process read snapshot from committed database state."""
        self._load()

    def add_ticket(self, ticket: Ticket) -> None:
        super().add_ticket(ticket)
        with Session(self.engine) as session:
            session.add(
                TicketRow(
                    id=str(ticket.id), request_id=ticket.request_id, idempotency_key=None,
                    source=ticket.source, text=ticket.text, taxonomy_version=ticket.taxonomy_version,
                    language=ticket.language or "unknown", status=ticket.status.value, risk=ticket.risk,
                    metadata_json=ticket.metadata, trace_id=ticket.trace_id, created_at=ticket.created_at,
                )
            )
            session.commit()

    def save_idempotency(self, key: str, payload: object, response: dict[str, object], ticket_id: UUID | None) -> None:
        super().save_idempotency(key, payload, response, ticket_id)
        record = self.idempotency[key]
        with Session(self.engine) as session:
            if ticket_id is not None:
                ticket = session.get(TicketRow, str(ticket_id))
                if ticket is not None:
                    ticket.idempotency_key = key
            session.merge(IdempotencyRow(key=key, fingerprint=record.fingerprint, response=response, ticket_id=str(ticket_id) if ticket_id else None))
            session.commit()

    def delete_ticket(self, ticket_id: UUID) -> None:
        ticket_key = str(ticket_id)
        with Session(self.engine) as session:
            session.execute(delete(ReviewDecisionRow).where(ReviewDecisionRow.ticket_id == ticket_key))
            session.execute(delete(LLMSuggestionRow).where(LLMSuggestionRow.ticket_id == ticket_key))
            session.execute(delete(PredictionRow).where(PredictionRow.ticket_id == ticket_key))
            session.execute(delete(IdempotencyRow).where(IdempotencyRow.ticket_id == ticket_key))
            session.execute(delete(TicketRow).where(TicketRow.id == ticket_key))
            session.commit()
        super().delete_ticket(ticket_id)

    def add_prediction(self, prediction: Prediction) -> None:
        super().add_prediction(prediction)
        with Session(self.engine) as session:
            session.add(
                PredictionRow(
                    id=str(prediction.id), ticket_id=str(prediction.ticket_id), decision=prediction.decision.value,
                    labels=list(prediction.labels), reason_codes=list(prediction.reason_codes),
                    route_primary=prediction.route_primary, route_collaborators=list(prediction.route_collaborators),
                    route_version=prediction.route_version, model_version=prediction.model_version,
                    threshold_version=prediction.threshold_version, taxonomy_version=prediction.taxonomy_version,
                    inference_ms=prediction.inference_ms, trace_id=prediction.trace_id,
                    language=prediction.language, created_at=prediction.created_at,
                )
            )
            session.commit()

    def add_review(self, review: ReviewDecision) -> None:
        super().add_review(review)
        with Session(self.engine) as session:
            session.add(
                ReviewDecisionRow(
                    id=str(review.id), ticket_id=str(review.ticket_id), revision=review.revision,
                    labels=list(review.labels), primary_queue=review.primary_queue, note=review.note,
                    reviewer_id=review.reviewer_id, training_candidate=review.training_candidate,
                    trace_id=review.trace_id, created_at=review.created_at,
                )
            )
            session.commit()

    def add_audit(self, event: AuditEvent) -> None:
        super().add_audit(event)
        with Session(self.engine) as session:
            session.add(
                AuditEventRow(
                    event_id=str(event.event_id), event_type=event.event_type, request_id=event.request_id,
                    trace_id=event.trace_id, actor=event.actor, subject=event.subject,
                    payload=event.payload, event_version=event.event_version, occurred_at=event.occurred_at,
                )
            )
            session.commit()

    def add_outbox(self, event: OutboxEvent) -> None:
        super().add_outbox(event)
        with Session(self.engine) as session:
            session.add(
                OutboxEventRow(
                    event_id=str(event.event_id), event_type=event.event_type, aggregate_id=event.aggregate_id,
                    payload=event.payload, created_at=event.created_at, published_at=event.published_at,
                )
            )
            session.commit()

    def add_llm_suggestion(self, suggestion: dict[str, object]) -> None:
        super().add_llm_suggestion(suggestion)
        with Session(self.engine) as session:
            session.add(
                LLMSuggestionRow(
                    id=str(uuid4()), ticket_id=str(suggestion.get("ticket_id", "")),
                    labels=list(suggestion.get("labels", [])), rationale=str(suggestion.get("rationale", "")),
                    confidence=float(suggestion.get("confidence", 0)), provider=str(suggestion.get("provider", "")),
                    model=str(suggestion.get("model", "")), prompt_version=str(suggestion.get("prompt_version", "")),
                    cost_usd=float(suggestion.get("cost_usd", 0)), created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

    def persist_ticket(self, ticket: Ticket) -> None:
        with Session(self.engine) as session:
            row = session.get(TicketRow, str(ticket.id))
            if row is not None:
                row.text, row.language, row.risk, row.status = ticket.text, ticket.language or "unknown", ticket.risk, ticket.status.value
                session.commit()

    def close(self) -> None:
        self.engine.dispose()
