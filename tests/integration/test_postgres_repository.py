from invoiceops.adapters.postgres import PostgresRepository
from invoiceops.application.service import TriageService


def test_sqlalchemy_repository_survives_process_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'business.sqlite3'}"
    first_repository = PostgresRepository(database_url)
    first = TriageService(repository=first_repository)
    response = first.classify(
        request_id="sql-restart-1",
        source="email",
        text="Please change the supplier bank account details.",
        taxonomy_version="invoiceops-v1",
        metadata={"channel": "email"},
        idempotency_key="sql-restart-key",
        trace_id="sql-trace-1",
    )

    second_repository = PostgresRepository(database_url)
    second = TriageService(repository=second_repository)
    replay = second.classify(
        request_id="sql-restart-1",
        source="email",
        text="Please change the supplier bank account details.",
        taxonomy_version="invoiceops-v1",
        metadata={"channel": "email"},
        idempotency_key="sql-restart-key",
        trace_id="sql-trace-2",
    )

    assert replay == response
    assert len(second_repository.tickets) == 1
    assert len(second_repository.predictions) == 1
    ticket = second_repository.get_ticket_by_request("sql-restart-1")
    assert ticket is not None
    assert ticket.language == "en"
    assert ticket.risk == "high"
    assert ticket.status.value == "needs_review"


def test_delete_idempotency_survives_ticket_removal(tmp_path) -> None:
    repository = PostgresRepository(f"sqlite:///{tmp_path / 'delete.sqlite3'}")
    service = TriageService(repository=repository)
    service.classify(
        request_id="sql-delete-1",
        source="email",
        text="Payment is pending.",
        taxonomy_version="invoiceops-v1",
        metadata={"channel": "email"},
        idempotency_key="sql-delete-create",
        trace_id="sql-delete-trace",
    )
    ticket = repository.get_ticket_by_request("sql-delete-1")
    assert ticket is not None

    deleted = service.delete_tickets(
        [ticket.id],
        idempotency_key="sql-delete-operation",
        actor="admin",
        trace_id="sql-delete-delete-trace",
    )

    assert deleted["deleted_count"] == 1
    assert repository.get_ticket_by_request("sql-delete-1") is None
    reloaded = PostgresRepository(f"sqlite:///{tmp_path / 'delete.sqlite3'}")
    replay = TriageService(repository=reloaded).delete_tickets(
        [ticket.id],
        idempotency_key="sql-delete-operation",
        actor="admin",
        trace_id="sql-delete-replay-trace",
    )
    assert replay == deleted
