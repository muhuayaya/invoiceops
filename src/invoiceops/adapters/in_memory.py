from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from invoiceops.domain.entities import AuditEvent, OutboxEvent, Prediction, ReviewDecision, Ticket


@dataclass(frozen=True)
class IdempotencyRecord:
    fingerprint: str
    response: dict[str, object]
    ticket_id: UUID | None


class InMemoryRepository:
    """Deterministic repository for the local API and fast integration tests."""

    def __init__(self) -> None:
        self.tickets: dict[UUID, Ticket] = {}
        self.tickets_by_request: dict[str, UUID] = {}
        self.predictions: dict[UUID, Prediction] = {}
        self.reviews: dict[UUID, list[ReviewDecision]] = {}
        self.audit_events: list[AuditEvent] = []
        self.outbox_events: list[OutboxEvent] = []
        self.llm_suggestions: list[dict[str, object]] = []
        self.idempotency: dict[str, IdempotencyRecord] = {}

    @staticmethod
    def fingerprint(payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def find_idempotency(self, key: str) -> IdempotencyRecord | None:
        return self.idempotency.get(key)

    def save_idempotency(self, key: str, payload: object, response: dict[str, object], ticket_id: UUID | None) -> None:
        self.idempotency[key] = IdempotencyRecord(self.fingerprint(payload), response, ticket_id)

    def add_ticket(self, ticket: Ticket) -> None:
        self.tickets[ticket.id] = ticket
        self.tickets_by_request[ticket.request_id] = ticket.id

    def get_ticket(self, ticket_id: UUID) -> Ticket | None:
        return self.tickets.get(ticket_id)

    def get_ticket_by_request(self, request_id: str) -> Ticket | None:
        ticket_id = self.tickets_by_request.get(request_id)
        return self.tickets.get(ticket_id) if ticket_id else None

    def delete_ticket(self, ticket_id: UUID) -> None:
        ticket = self.tickets.pop(ticket_id, None)
        if ticket is None:
            return
        self.tickets_by_request.pop(ticket.request_id, None)
        self.predictions.pop(ticket_id, None)
        self.reviews.pop(ticket_id, None)
        self.llm_suggestions = [item for item in self.llm_suggestions if item.get("ticket_id") != str(ticket_id)]
        self.idempotency = {
            key: record for key, record in self.idempotency.items() if record.ticket_id != ticket_id
        }

    def add_prediction(self, prediction: Prediction) -> None:
        self.predictions[prediction.ticket_id] = prediction

    def get_prediction(self, ticket_id: UUID) -> Prediction | None:
        return self.predictions.get(ticket_id)

    def add_review(self, review: ReviewDecision) -> None:
        self.reviews.setdefault(review.ticket_id, []).append(review)

    def list_reviews(self, risk: str | None = None, label_code: str | None = None, limit: int = 50) -> list[tuple[Ticket, Prediction, list[ReviewDecision]]]:
        items: list[tuple[Ticket, Prediction, list[ReviewDecision]]] = []
        for ticket_id, prediction in self.predictions.items():
            ticket = self.tickets[ticket_id]
            if prediction.decision.value != "needs_review":
                continue
            reviews = self.reviews.get(ticket_id, [])
            if reviews:
                continue
            labels = [label for label, _ in prediction.labels]
            if risk and risk != getattr(ticket, "risk", None):
                # Risk is calculated from the prediction labels by the service and stored on the ticket.
                continue
            if label_code and label_code not in labels:
                continue
            items.append((ticket, prediction, reviews))
        items.sort(key=lambda item: (getattr(item[0], "risk", "medium") != "high", item[0].created_at))
        return items[:limit]

    def add_audit(self, event: AuditEvent) -> None:
        self.audit_events.append(event)

    def audit_for_request(self, request_id: str) -> list[AuditEvent]:
        return [event for event in self.audit_events if event.request_id == request_id]

    def add_outbox(self, event: OutboxEvent) -> None:
        self.outbox_events.append(event)

    def add_llm_suggestion(self, suggestion: dict[str, object]) -> None:
        self.llm_suggestions.append(suggestion)
