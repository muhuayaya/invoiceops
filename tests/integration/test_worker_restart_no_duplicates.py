from invoiceops.adapters.sqlite import SQLiteRepository
from invoiceops.application.service import TriageService
from invoiceops.batch import BatchProcessor
from invoiceops.batch_store import PersistentBatchStore


def test_worker_restart_reuses_durable_business_record(tmp_path) -> None:
    data_path = tmp_path / "business.sqlite3"
    batch_path = tmp_path / "batches.sqlite3"
    content = b"request_id,source,text,taxonomy_version\nrestart-record,email,Payment status?,invoiceops-v1\n"

    first_repository = SQLiteRepository(data_path)
    first_store = PersistentBatchStore(batch_path)
    first_processor = BatchProcessor(TriageService(repository=first_repository), first_store)
    job = first_processor.create(content)

    # Simulate a process crash after the business transaction completed but
    # before the batch checkpoint was saved.
    first_processor.service.classify(
        request_id="restart-record",
        source="email",
        text="Payment status?",
        taxonomy_version="invoiceops-v1",
        metadata={"channel": ""},
        idempotency_key=f"batch:{job.batch_id}:2",
        trace_id="trace-before-crash",
    )

    second_repository = SQLiteRepository(data_path)
    second_processor = BatchProcessor(TriageService(repository=second_repository), PersistentBatchStore(batch_path))
    recovered = second_processor.process(job.batch_id)

    assert recovered.status.value == "completed"
    assert recovered.success_rows == 1
    assert len(second_repository.tickets) == 1
    assert len(second_repository.predictions) == 1
    ticket = second_repository.get_ticket_by_request("restart-record")
    assert ticket is not None
    assert ticket.language == "en"
    assert ticket.status.value == "needs_review"
