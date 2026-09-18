from datetime import datetime, timedelta, timezone
from uuid import uuid4

from invoiceops.adapters.in_memory import InMemoryRepository
from invoiceops.domain.entities import Ticket
from invoiceops.observability.logging import JsonFormatter
from invoiceops.retention import RetentionPolicy, cleanup_in_memory


def test_structured_log_formatter_has_trace_fields_without_ticket_text() -> None:
    import logging

    record = logging.LogRecord("invoiceops", logging.INFO, "", 0, "request completed", (), None)
    record.trace_id = "trace-1"
    record.stage = "request"
    record.result_code = "200"
    rendered = JsonFormatter().format(record)
    assert '"trace_id":"trace-1"' in rendered
    assert '"stage":"request"' in rendered
    assert "supplier bank account" not in rendered


def test_structured_log_formatter_redacts_credentials_and_email() -> None:
    import logging

    record = logging.LogRecord("invoiceops", logging.WARNING, "", 0, "password=secret user=a@example.com", (), None)
    rendered = JsonFormatter().format(record)
    assert "secret" not in rendered
    assert "a@example.com" not in rendered


def test_retention_redacts_expired_text_and_returns_proof() -> None:
    repository = InMemoryRepository()
    ticket = Ticket(uuid4(), "retention-1", "api", "old supplier bank account", "invoiceops-v1", {}, trace_id="trace")
    ticket.created_at = datetime.now(timezone.utc) - timedelta(days=40)
    repository.add_ticket(ticket)
    result = cleanup_in_memory(repository, RetentionPolicy(sanitized_text_days=30))
    assert ticket.text == "[REDACTED_AFTER_RETENTION]"
    assert result["cleaned_tickets"] == 1
    assert result["audit_proof"]
