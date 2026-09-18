from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

from invoiceops.adapters.in_memory import IdempotencyRecord, InMemoryRepository
from invoiceops.domain.entities import (
    AuditEvent,
    OutboxEvent,
    Prediction,
    ReviewDecision,
    Ticket,
    TicketStatus,
)


class SQLiteRepository(InMemoryRepository):
    """Durable local repository used by the worker recovery path.

    PostgreSQL remains the production persistence target described by the
    design. This adapter gives the local Compose/fallback path process-safe
    idempotency and business-record recovery without changing the domain API.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT UNIQUE,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    taxonomy_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    language TEXT,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    route_primary TEXT NOT NULL,
                    route_collaborators_json TEXT NOT NULL,
                    route_version TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    threshold_version TEXT NOT NULL,
                    taxonomy_version TEXT NOT NULL,
                    language TEXT NOT NULL,
                    inference_ms REAL NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    labels_json TEXT NOT NULL,
                    primary_queue TEXT NOT NULL,
                    note TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    training_candidate INTEGER NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticket_id, revision)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    event_version TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE TABLE IF NOT EXISTS llm_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    suggestion_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    ticket_id TEXT
                );
                """
            )
        self._load()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _datetime(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _load(self) -> None:
        with self._connection() as connection:
            for row in connection.execute("SELECT * FROM tickets"):
                ticket = Ticket(
                    id=UUID(row["id"]),
                    request_id=row["request_id"],
                    source=row["source"],
                    text=row["text"],
                    taxonomy_version=row["taxonomy_version"],
                    metadata=json.loads(row["metadata_json"]),
                    language=row["language"],
                    risk=row["risk"],
                    status=TicketStatus(row["status"]),
                    trace_id=row["trace_id"],
                    created_at=self._datetime(row["created_at"]),
                )
                super().add_ticket(ticket)
            for row in connection.execute("SELECT * FROM predictions"):
                prediction = Prediction(
                    id=UUID(row["id"]),
                    ticket_id=UUID(row["ticket_id"]),
                    decision=TicketStatus(row["decision"]),
                    labels=tuple((str(code), float(score)) for code, score in json.loads(row["labels_json"])),
                    reason_codes=tuple(json.loads(row["reason_codes_json"])),
                    route_primary=row["route_primary"],
                    route_collaborators=tuple(json.loads(row["route_collaborators_json"])),
                    route_version=row["route_version"],
                    model_version=row["model_version"],
                    threshold_version=row["threshold_version"],
                    taxonomy_version=row["taxonomy_version"],
                    language=row["language"],
                    inference_ms=float(row["inference_ms"]),
                    trace_id=row["trace_id"],
                    created_at=self._datetime(row["created_at"]),
                )
                super().add_prediction(prediction)
            for row in connection.execute("SELECT * FROM reviews ORDER BY ticket_id, revision"):
                super().add_review(
                    ReviewDecision(
                        id=UUID(row["id"]),
                        ticket_id=UUID(row["ticket_id"]),
                        revision=int(row["revision"]),
                        labels=tuple(json.loads(row["labels_json"])),
                        primary_queue=row["primary_queue"],
                        note=row["note"],
                        reviewer_id=row["reviewer_id"],
                        training_candidate=bool(row["training_candidate"]),
                        created_at=self._datetime(row["created_at"]),
                        trace_id=row["trace_id"],
                    )
                )
            for row in connection.execute("SELECT * FROM audit_events ORDER BY occurred_at, rowid"):
                super().add_audit(
                    AuditEvent(
                        event_id=UUID(row["event_id"]),
                        event_type=row["event_type"],
                        request_id=row["request_id"],
                        trace_id=row["trace_id"],
                        actor=row["actor"],
                        subject=row["subject"],
                        payload=json.loads(row["payload_json"]),
                        occurred_at=self._datetime(row["occurred_at"]),
                        event_version=row["event_version"],
                    )
                )
            for row in connection.execute("SELECT * FROM outbox_events ORDER BY created_at, rowid"):
                super().add_outbox(
                    OutboxEvent(
                        event_id=UUID(row["event_id"]),
                        event_type=row["event_type"],
                        aggregate_id=row["aggregate_id"],
                        payload=json.loads(row["payload_json"]),
                        created_at=self._datetime(row["created_at"]),
                        published_at=self._datetime(row["published_at"]) if row["published_at"] else None,
                    )
                )
            for row in connection.execute("SELECT suggestion_json FROM llm_suggestions ORDER BY id"):
                self.llm_suggestions.append(json.loads(row["suggestion_json"]))
            for row in connection.execute("SELECT * FROM idempotency"):
                self.idempotency[row["key"]] = IdempotencyRecord(
                    row["fingerprint"], json.loads(row["response_json"]),
                    UUID(row["ticket_id"]) if row["ticket_id"] else None,
                )

    def add_ticket(self, ticket: Ticket) -> None:
        super().add_ticket(ticket)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(ticket.id), ticket.request_id, None, ticket.source, ticket.text,
                    ticket.taxonomy_version, json.dumps(ticket.metadata, ensure_ascii=False),
                    ticket.language, ticket.risk, ticket.status.value, ticket.trace_id,
                    ticket.created_at.isoformat(),
                ),
            )

    def save_idempotency(self, key: str, payload: object, response: dict[str, object], ticket_id: UUID | None) -> None:
        super().save_idempotency(key, payload, response, ticket_id)
        record = self.idempotency[key]
        with self._connection() as connection:
            if ticket_id is not None:
                connection.execute("UPDATE tickets SET idempotency_key=? WHERE id=?", (key, str(ticket_id)))
            connection.execute(
                "INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?)",
                (key, record.fingerprint, json.dumps(response, ensure_ascii=False), str(ticket_id) if ticket_id else None),
            )

    def delete_ticket(self, ticket_id: UUID) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM reviews WHERE ticket_id = ?", (str(ticket_id),))
            connection.execute("DELETE FROM predictions WHERE ticket_id = ?", (str(ticket_id),))
            connection.execute("DELETE FROM llm_suggestions WHERE ticket_id = ?", (str(ticket_id),))
            connection.execute("DELETE FROM idempotency WHERE ticket_id = ?", (str(ticket_id),))
            connection.execute("DELETE FROM tickets WHERE id = ?", (str(ticket_id),))
        super().delete_ticket(ticket_id)

    def add_prediction(self, prediction: Prediction) -> None:
        super().add_prediction(prediction)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(prediction.id), str(prediction.ticket_id), prediction.decision.value,
                    json.dumps(prediction.labels), json.dumps(prediction.reason_codes),
                    prediction.route_primary, json.dumps(prediction.route_collaborators),
                    prediction.route_version, prediction.model_version, prediction.threshold_version,
                    prediction.taxonomy_version, prediction.language, prediction.inference_ms,
                    prediction.trace_id, prediction.created_at.isoformat(),
                ),
            )

    def add_review(self, review: ReviewDecision) -> None:
        super().add_review(review)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(review.id), str(review.ticket_id), review.revision, json.dumps(review.labels),
                    review.primary_queue, review.note, review.reviewer_id, int(review.training_candidate),
                    review.trace_id, review.created_at.isoformat(),
                ),
            )

    def add_audit(self, event: AuditEvent) -> None:
        super().add_audit(event)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(event.event_id), event.event_type, event.request_id, event.trace_id,
                    event.actor, event.subject, json.dumps(event.payload, ensure_ascii=False),
                    event.event_version, event.occurred_at.isoformat(),
                ),
            )

    def add_outbox(self, event: OutboxEvent) -> None:
        super().add_outbox(event)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO outbox_events VALUES (?,?,?,?,?,?)",
                (
                    str(event.event_id), event.event_type, event.aggregate_id,
                    json.dumps(event.payload, ensure_ascii=False), event.created_at.isoformat(),
                    event.published_at.isoformat() if event.published_at else None,
                ),
            )

    def add_llm_suggestion(self, suggestion: dict[str, object]) -> None:
        super().add_llm_suggestion(suggestion)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO llm_suggestions(ticket_id, suggestion_json) VALUES (?,?)",
                (str(suggestion.get("ticket_id", "")), json.dumps(suggestion, ensure_ascii=False)),
            )

    def persist_ticket(self, ticket: Ticket) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE tickets SET text=?,language=?,risk=?,status=? WHERE id=?",
                (ticket.text, ticket.language, ticket.risk, ticket.status.value, str(ticket.id)),
            )
