from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from invoiceops.adapters.in_memory import InMemoryRepository
from invoiceops.adapters.taxonomy import TaxonomyCatalog
from invoiceops.domain.entities import AuditEvent, OutboxEvent, Prediction, ReviewDecision, Ticket, TicketStatus
from invoiceops.domain.errors import DomainError, ValidationError
from invoiceops.ml_runtime.thresholds import DecisionPolicy, ThresholdPolicy
from invoiceops.llm.policy import LLMPolicy
from invoiceops.llm.provider import LLMProvider


class IdempotencyConflict(DomainError):
    pass


class NotFound(DomainError):
    pass


_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_ACCOUNT = re.compile(r"\b(?:IBAN\s*)?[A-Z]{2}\d{2}[A-Z0-9]{10,34}\b", re.I)
_INVOICE = re.compile(r"\b(?:INV|发票)[-_ ]?[A-Z0-9-]{3,}\b", re.I)


def sanitize_text(text: str) -> str:
    """Minimal deterministic masking for persisted/model input in the PoC."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    text = _ACCOUNT.sub("[ACCOUNT]", text)
    return _INVOICE.sub("[INVOICE]", text)


class TriageService:
    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        classifier=None,
        taxonomy: TaxonomyCatalog | None = None,
        policy: DecisionPolicy | None = None,
        llm_policy: LLMPolicy | None = None,
        llm_provider: LLMProvider | None = None,
        review_sla_minutes: int | None = None,
    ) -> None:
        from invoiceops.ml_runtime.rules import KeywordClassifier

        self.repository = repository or InMemoryRepository()
        self.taxonomy = taxonomy or TaxonomyCatalog()
        self.classifier = classifier or KeywordClassifier()
        if policy is None:
            root = __import__("pathlib").Path(__file__).resolve().parents[3]
            threshold = ThresholdPolicy.from_file(root / "configs/thresholds/thresholds-v1.json")
            policy = DecisionPolicy(threshold)
        self.policy = policy
        self.llm_policy = llm_policy
        self.llm_provider = llm_provider
        self.llm_fallback_total = 0
        configured_sla = review_sla_minutes or int(os.getenv("INVOICEOPS_HIGH_RISK_REVIEW_SLA_MINUTES", "60"))
        self.review_sla_minutes = max(1, configured_sla)

    def classify(
        self,
        *,
        request_id: str,
        source: str,
        text: str,
        taxonomy_version: str,
        metadata: dict[str, str],
        idempotency_key: str,
        trace_id: str,
        actor: str = "api",
    ) -> dict[str, object]:
        payload = {
            "request_id": request_id,
            "source": source,
            "text": text,
            "taxonomy_version": taxonomy_version,
            "metadata": metadata,
        }
        fingerprint = self.repository.fingerprint(payload)
        previous = self.repository.find_idempotency(idempotency_key)
        if previous:
            if previous.fingerprint != fingerprint:
                raise IdempotencyConflict("idempotency key was already used with a different request")
            return previous.response

        if self.repository.get_ticket_by_request(request_id):
            raise IdempotencyConflict("request_id already exists")
        self._validate_classification_data(request_id, source, text, taxonomy_version)

        ticket_id = uuid4()
        sanitized = sanitize_text(text)
        ticket = Ticket(
            id=ticket_id,
            request_id=request_id,
            source=source,
            text=sanitized,
            taxonomy_version=taxonomy_version,
            metadata=dict(metadata),
            trace_id=trace_id,
        )
        self.repository.add_ticket(ticket)
        self._event("ticket.received", request_id, trace_id, actor, str(ticket_id), {"source": source})

        result = self.classifier.predict(sanitized, sanitized=True)
        decision, reasons = self.policy.decide(result)
        candidate_labels = [
            (item.label_code, item.score)
            for item in result.predictions
            if item.score >= self.policy.thresholds.candidate_threshold
        ]
        labels_for_route = [label for label, _ in candidate_labels]
        route_primary, collaborators, route_version = self.taxonomy.route_for(labels_for_route)
        ticket.language = result.language
        ticket.risk = self.taxonomy.risk_for(labels_for_route)
        target_status = TicketStatus.CLASSIFIED if decision == "classified" else TicketStatus.NEEDS_REVIEW
        ticket.transition_to(target_status)
        persist_ticket = getattr(self.repository, "persist_ticket", None)
        if persist_ticket:
            persist_ticket(ticket)
        prediction = Prediction(
            id=uuid4(),
            ticket_id=ticket_id,
            decision=target_status,
            labels=tuple(candidate_labels),
            reason_codes=tuple(reasons),
            route_primary=route_primary,
            route_collaborators=collaborators,
            route_version=route_version,
            model_version=result.model_version,
            threshold_version=result.threshold_version,
            taxonomy_version=taxonomy_version,
            language=result.language,
            inference_ms=result.inference_ms,
            trace_id=trace_id,
        )
        self.repository.add_prediction(prediction)
        self._event("prediction.created", request_id, trace_id, "classifier", str(prediction.id), {"decision": decision})
        self._event("route.recommended", request_id, trace_id, "router", str(ticket_id), {"primary": route_primary, "version": route_version})

        if decision == "needs_review" and self.llm_policy and self.llm_provider:
            suggestion = self.llm_policy.request(
                self.llm_provider,
                sanitized=True,
                text=sanitized,
                allowed_labels=tuple(self.taxonomy.label_codes),
                prompt_version="invoiceops-prompt-v1",
            )
            if suggestion:
                self.repository.add_llm_suggestion({
                    "ticket_id": str(ticket_id), "labels": list(suggestion.labels), "rationale": suggestion.rationale,
                    "confidence": suggestion.confidence, "provider": suggestion.provider, "model": suggestion.model,
                    "prompt_version": suggestion.prompt_version, "cost_usd": suggestion.cost_usd,
                })
                self._event("llm.suggestion.created", request_id, trace_id, suggestion.provider, str(ticket_id), {"model": suggestion.model, "prompt_version": suggestion.prompt_version})
            else:
                self.llm_fallback_total += 1
                self._event("llm.fallback", request_id, trace_id, "llm-policy", str(ticket_id), {"reason": "suggestion_unavailable"})

        response = self._prediction_response(ticket, prediction)
        self.repository.save_idempotency(idempotency_key, payload, response, ticket_id)
        self.repository.add_outbox(OutboxEvent(uuid4(), "classification.completed", str(ticket_id), response))
        return response

    def delete_tickets(
        self,
        ticket_ids: list[UUID],
        *,
        idempotency_key: str,
        actor: str,
        trace_id: str,
    ) -> dict[str, object]:
        if not ticket_ids or len(set(ticket_ids)) != len(ticket_ids):
            raise ValidationError("ticket_ids must contain at least one unique ticket")
        payload = {"operation": "delete_tickets", "ticket_ids": [str(ticket_id) for ticket_id in ticket_ids]}
        previous = self.repository.find_idempotency(idempotency_key)
        if previous:
            if previous.fingerprint != self.repository.fingerprint(payload):
                raise IdempotencyConflict("idempotency key was already used with a different operation")
            return previous.response

        tickets = [self.repository.get_ticket(ticket_id) for ticket_id in ticket_ids]
        if any(ticket is None for ticket in tickets):
            raise NotFound("one or more tickets not found")
        existing_tickets = [ticket for ticket in tickets if ticket is not None]
        for ticket in existing_tickets:
            self._event(
                "ticket.deleted",
                ticket.request_id,
                trace_id,
                actor,
                str(ticket.id),
                {"ticket_id": str(ticket.id), "request_id": ticket.request_id},
            )
        for ticket in existing_tickets:
            self.repository.delete_ticket(ticket.id)

        response = {
            "deleted_count": len(existing_tickets),
            "ticket_ids": [str(ticket.id) for ticket in existing_tickets],
            "trace_id": trace_id,
        }
        self.repository.save_idempotency(idempotency_key, payload, response, None)
        self.repository.add_outbox(OutboxEvent(uuid4(), "tickets.deleted", "batch", response))
        return response

    def overwrite_tickets(
        self,
        items: list[dict[str, object]],
        *,
        idempotency_key: str,
        actor: str,
        trace_id: str,
    ) -> dict[str, object]:
        if not items:
            raise ValidationError("overwrite items must not be empty")
        payload = {"operation": "batch_overwrite", "items": items}
        previous = self.repository.find_idempotency(idempotency_key)
        if previous:
            if previous.fingerprint != self.repository.fingerprint(payload):
                raise IdempotencyConflict("idempotency key was already used with a different operation")
            return previous.response

        request_ids = [str(item.get("request_id", "")) for item in items]
        if len(set(request_ids)) != len(request_ids):
            raise ValidationError("overwrite request_id values must be unique")
        existing = []
        for item in items:
            request_id = str(item.get("request_id", ""))
            ticket = self.repository.get_ticket_by_request(request_id)
            if ticket is None:
                raise NotFound(f"ticket not found for request_id: {request_id}")
            self._validate_classification_data(
                request_id,
                str(item.get("source", "")),
                str(item.get("text", "")),
                str(item.get("taxonomy_version", "")),
            )
            existing.append(ticket)

        results = []
        for index, (item, previous_ticket) in enumerate(zip(items, existing, strict=True)):
            self.repository.delete_ticket(previous_ticket.id)
            derived_key = "admin-overwrite-" + sha256(
                f"{idempotency_key}:{index}:{previous_ticket.id}".encode("utf-8")
            ).hexdigest()
            result = self.classify(
                request_id=str(item["request_id"]),
                source=str(item["source"]),
                text=str(item["text"]),
                taxonomy_version=str(item["taxonomy_version"]),
                metadata=dict(item.get("metadata") or {}),
                idempotency_key=derived_key,
                trace_id=trace_id,
                actor=actor,
            )
            new_ticket = self.repository.get_ticket_by_request(str(item["request_id"]))
            self._event(
                "ticket.overwritten",
                str(item["request_id"]),
                trace_id,
                actor,
                str(new_ticket.id) if new_ticket else "unknown",
                {
                    "previous_ticket_id": str(previous_ticket.id),
                    "new_ticket_id": str(new_ticket.id) if new_ticket else "unknown",
                },
            )
            results.append(result)

        response = {"overwritten_count": len(results), "items": results, "trace_id": trace_id}
        first_ticket = self.repository.get_ticket_by_request(request_ids[0])
        if first_ticket is None:
            raise NotFound("overwritten ticket not found")
        self.repository.save_idempotency(idempotency_key, payload, response, first_ticket.id)
        self.repository.add_outbox(OutboxEvent(uuid4(), "tickets.batch_overwritten", "batch", response))
        return response

    def list_reviews(
        self,
        risk: str | None,
        label_code: str | None,
        limit: int,
        waiting_min_seconds: int | None = None,
    ) -> list[dict[str, object]]:
        now = datetime.now(timezone.utc)
        items = []
        repository_limit = 1_000_000 if waiting_min_seconds is not None else limit
        for ticket, prediction, _ in self.repository.list_reviews(risk, label_code, repository_limit):
            waiting_seconds = max(0, int((now - ticket.created_at).total_seconds()))
            overdue = ticket.risk == "high" and waiting_seconds >= self.review_sla_minutes * 60
            if waiting_min_seconds is not None and waiting_seconds < waiting_min_seconds:
                continue
            if overdue and not any(event.event_type == "review.sla_breached" for event in self.repository.audit_for_request(ticket.request_id)):
                self._event(
                    "review.sla_breached",
                    ticket.request_id,
                    str(uuid4()),
                    "system",
                    str(ticket.id),
                    {"waiting_seconds": waiting_seconds, "sla_minutes": 60, "risk": ticket.risk},
                )
                self.repository.add_outbox(OutboxEvent(uuid4(), "review.sla_breached", str(ticket.id), {"waiting_seconds": waiting_seconds}))
            items.append(
                {
                    "ticket_id": str(ticket.id),
                    "request_id": ticket.request_id,
                    "risk": ticket.risk,
                    "decision": prediction.decision.value,
                    "primary_queue": prediction.route_primary,
                    "collaborator_queues": list(prediction.route_collaborators),
                    "predictions": [{"label_code": code, "score": score} for code, score in prediction.labels],
                    "reason_codes": list(prediction.reason_codes),
                    "sanitized_text": ticket.text,
                    "waiting_seconds": waiting_seconds,
                    "overdue": overdue,
                    "review_sla_minutes": self.review_sla_minutes,
                }
            )
        return items[:limit]

    def review_history(self, ticket_id: UUID) -> list[dict[str, object]]:
        ticket = self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise NotFound("ticket not found")
        return [
            {
                "ticket_id": str(review.ticket_id),
                "revision": review.revision,
                "labels": list(review.labels),
                "primary_queue": review.primary_queue,
                "note": review.note,
                "reviewer_id": review.reviewer_id,
                "training_candidate": review.training_candidate,
                "created_at": review.created_at.isoformat(),
                "trace_id": review.trace_id,
            }
            for review in self.repository.reviews.get(ticket_id, [])
        ]

    def list_tickets(
        self,
        request_id: str | None = None,
        risk: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        items = []
        for ticket in self.repository.tickets.values():
            if request_id and request_id.casefold() not in ticket.request_id.casefold():
                continue
            if risk and ticket.risk != risk:
                continue
            if status and ticket.status.value != status:
                continue
            prediction = self.repository.get_prediction(ticket.id)
            if prediction is None:
                continue
            latest_review = self.repository.reviews.get(ticket.id, [])[-1:]
            review = latest_review[0] if latest_review else None
            items.append(
                {
                    "ticket_id": str(ticket.id),
                    "request_id": ticket.request_id,
                    "source": ticket.source,
                    "sanitized_text": ticket.text,
                    "language": ticket.language or "unknown",
                    "risk": ticket.risk,
                    "status": ticket.status.value,
                    "labels": list(review.labels) if review else [code for code, _ in prediction.labels],
                    "primary_queue": review.primary_queue if review else prediction.route_primary,
                    "created_at": ticket.created_at.isoformat(),
                }
            )
        items.sort(key=lambda item: str(item["created_at"]), reverse=True)
        return items[:limit]

    def review(
        self,
        ticket_id: UUID,
        labels: list[str],
        primary_queue: str,
        note: str,
        reviewer_id: str,
        trace_id: str,
    ) -> dict[str, object]:
        ticket = self.repository.get_ticket(ticket_id)
        prediction = self.repository.get_prediction(ticket_id)
        if ticket is None or prediction is None:
            raise NotFound("ticket not found")
        self.taxonomy.validate_labels(labels)
        self.taxonomy.validate_queue(primary_queue)
        revision = len(self.repository.reviews.get(ticket_id, [])) + 1
        decision = ReviewDecision.create(ticket_id, revision, labels, primary_queue, note, reviewer_id, trace_id)
        self.repository.add_review(decision)
        if ticket.status == TicketStatus.NEEDS_REVIEW:
            ticket.transition_to(TicketStatus.REVIEWED)
            persist_ticket = getattr(self.repository, "persist_ticket", None)
            if persist_ticket:
                persist_ticket(ticket)
        self._event("review.appended", ticket.request_id, trace_id, reviewer_id, str(ticket_id), {"revision": revision, "labels": labels})
        self.repository.add_outbox(OutboxEvent(uuid4(), "review.appended", str(ticket_id), {"revision": revision}))
        return {
            "ticket_id": str(ticket_id),
            "revision": revision,
            "labels": labels,
            "reviewer_id": reviewer_id,
            "trace_id": trace_id,
        }

    def audit(self, request_id: str) -> list[dict[str, object]]:
        events = self.repository.audit_for_request(request_id)
        if not events:
            raise NotFound("request not found")
        return [
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "event_version": event.event_version,
                "occurred_at": event.occurred_at.isoformat(),
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "actor": {"id": event.actor},
                "subject": {"id": event.subject},
                "payload": event.payload,
            }
            for event in events
        ]

    def record_model_event(self, event_type: str, model_version: str, actor: str = "system") -> None:
        self._event(event_type, "system", str(uuid4()), actor, model_version, {"model_version": model_version})
        self.repository.add_outbox(OutboxEvent(uuid4(), event_type, model_version, {"model_version": model_version}))

    def record_security_event(self, event_type: str, trace_id: str, actor: str, payload: dict[str, object]) -> None:
        self._event(event_type, "security", trace_id, actor, "security", payload)

    def _event(self, event_type: str, request_id: str, trace_id: str, actor: str, subject: str, payload: dict[str, object]) -> None:
        self.repository.add_audit(AuditEvent(uuid4(), event_type, request_id, trace_id, actor, subject, payload))

    def _validate_classification_data(self, request_id: str, source: str, text: str, taxonomy_version: str) -> None:
        if not request_id.strip():
            raise ValidationError("request_id must not be empty")
        if source not in {"email", "portal", "api", "manual"}:
            raise ValidationError("source is invalid")
        self.taxonomy.validate_version(taxonomy_version)
        if not text.strip():
            raise ValidationError("text must not be empty")
        if len(text) > 5000:
            raise ValidationError("text exceeds 5000 characters")

    @staticmethod
    def _prediction_response(ticket: Ticket, prediction: Prediction) -> dict[str, object]:
        return {
            "request_id": ticket.request_id,
            "decision": prediction.decision.value,
            "reason_codes": list(prediction.reason_codes),
            "language": prediction.language,
            "predictions": [{"label_code": code, "score": score} for code, score in prediction.labels],
            "route": {
                "primary": prediction.route_primary,
                "collaborators": list(prediction.route_collaborators),
                "version": prediction.route_version,
            },
            "model_version": prediction.model_version,
            "threshold_version": prediction.threshold_version,
            "taxonomy_version": prediction.taxonomy_version,
            "inference_ms": prediction.inference_ms,
            "trace_id": prediction.trace_id,
        }
