from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from .errors import InvalidTransition, ValidationError


class TicketStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    NEEDS_REVIEW = "needs_review"
    REVIEWED = "reviewed"


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LabelDefinition:
    code: str
    name: str
    risk: str
    description: str = ""


@dataclass
class Ticket:
    id: UUID
    request_id: str
    source: str
    text: str
    taxonomy_version: str
    metadata: dict[str, str] = field(default_factory=dict)
    language: str | None = None
    risk: str = "medium"
    status: TicketStatus = TicketStatus.RECEIVED
    trace_id: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def transition_to(self, target: TicketStatus) -> None:
        allowed = {
            TicketStatus.RECEIVED: {TicketStatus.CLASSIFIED, TicketStatus.NEEDS_REVIEW},
            TicketStatus.CLASSIFIED: {TicketStatus.NEEDS_REVIEW, TicketStatus.REVIEWED},
            TicketStatus.NEEDS_REVIEW: {TicketStatus.REVIEWED},
            TicketStatus.REVIEWED: set(),
        }
        if target not in allowed[self.status]:
            raise InvalidTransition(f"ticket {self.id} cannot transition {self.status} -> {target}")
        self.status = target


@dataclass(frozen=True)
class Prediction:
    id: UUID
    ticket_id: UUID
    decision: TicketStatus
    labels: tuple[tuple[str, float], ...]
    reason_codes: tuple[str, ...]
    route_primary: str
    route_collaborators: tuple[str, ...]
    route_version: str
    model_version: str
    threshold_version: str
    taxonomy_version: str
    language: str
    inference_ms: float
    trace_id: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ReviewDecision:
    id: UUID
    ticket_id: UUID
    revision: int
    labels: tuple[str, ...]
    primary_queue: str
    note: str
    reviewer_id: str
    training_candidate: bool
    created_at: datetime = field(default_factory=utc_now)
    trace_id: str = ""

    @classmethod
    def create(
        cls,
        ticket_id: UUID,
        revision: int,
        labels: list[str],
        primary_queue: str,
        note: str,
        reviewer_id: str,
        trace_id: str,
    ) -> "ReviewDecision":
        if revision < 1 or not labels or not primary_queue:
            raise ValidationError("review decision requires labels and a primary queue")
        return cls(
            id=uuid4(),
            ticket_id=ticket_id,
            revision=revision,
            labels=tuple(labels),
            primary_queue=primary_queue,
            note=note,
            reviewer_id=reviewer_id,
            training_candidate=True,
            trace_id=trace_id,
        )


@dataclass(frozen=True)
class ModelVersion:
    version: str
    model_type: str
    threshold_version: str
    status: ModelStatus = ModelStatus.CANDIDATE
    metrics: dict[str, float] = field(default_factory=dict)

    def activate(self) -> "ModelVersion":
        if self.status not in {ModelStatus.CANDIDATE, ModelStatus.ROLLED_BACK}:
            raise InvalidTransition(f"model {self.version} cannot become active from {self.status}")
        return ModelVersion(self.version, self.model_type, self.threshold_version, ModelStatus.ACTIVE, self.metrics)

    def rollback(self) -> "ModelVersion":
        if self.status != ModelStatus.ACTIVE:
            raise InvalidTransition(f"model {self.version} cannot be rolled back from {self.status}")
        return ModelVersion(self.version, self.model_type, self.threshold_version, ModelStatus.ROLLED_BACK, self.metrics)


@dataclass(frozen=True)
class AuditEvent:
    event_id: UUID
    event_type: str
    request_id: str
    trace_id: str
    actor: str
    subject: str
    payload: dict[str, object]
    occurred_at: datetime = field(default_factory=utc_now)
    event_version: str = "1"


@dataclass(frozen=True)
class OutboxEvent:
    event_id: UUID
    event_type: str
    aggregate_id: str
    payload: dict[str, object]
    created_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None
